"""Opt-in reuse of feedback-verified, explicitly requested literal rover recipes.

Only code is reused. Every invocation is revalidated and executed with fresh Runtime
requests, current sensor checks and fresh stopped feedback. Simulation benchmarks remain
unchanged. Ambiguous/context-dependent/sensor-derived requests always go to the model.
"""
import ast
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import threading
import unicodedata
from uuid import uuid4

from agent.physical_context import _summary
from agent.physical_policy import validate_physical_policy, _literal_number
from robot.hardware_limits import move_mm, reverse_mm, turn_tenths

CONTRACT='physical-explicit-literals-v2'
MAX_ENTRIES=128

def explicit_recipe(instruction):
    """Recognize only a complete, explicit motion request, never partial intent."""
    if not isinstance(instruction,str): return None
    text=re.sub(r'\s+','',unicodedata.normalize('NFKC',instruction).lower())
    clauses=[part for part in re.split(r'然后|接着|随后|最后|再|并且|then|and|[,，;；。]',text) if part]
    result=[]
    for index,clause in enumerate(clauses):
        if clause in ('停止','停下','停车','停','stop'):
            if index!=len(clauses)-1: return None
            continue
        match=re.fullmatch(r'(前进|向前走?|向前移动|往前走?|后退|向后走?|向后移动|往后走?|forward|backward|moveforward|movebackward)(\d+(?:\.\d+)?)(厘米|公分|毫米|米|cm|mm|m)',clause)
        try:
            if match:
                direction,number,unit=match.groups()
                distance=float(Decimal(number)/({'厘米':100,'公分':100,'cm':100,'毫米':1000,'mm':1000}.get(unit,1)))
                name='reverse' if direction in ('后退','向后','向后走','向后移动','往后','往后走','backward','movebackward') else 'move'
                result.append((name,{'move':move_mm,'reverse':reverse_mm}[name](distance)))
                continue
            match=re.fullmatch(r'(左转|向左转|往左转|右转|向右转|往右转|turnleft|turnright)(\d+(?:\.\d+)?)(度|°|degrees|deg)',clause)
            if not match: return None
            direction,number,_=match.groups()
            sign=-1 if direction in ('右转','向右转','往右转','turnright') else 1
            result.append(('turn',turn_tenths(sign*float(Decimal(number)))))
        except (ValueError,InvalidOperation,ArithmeticError): return None
        except Exception: return None  # Unsupported hardware range is never approximated.
    if not clauses or len(result)>9: return None
    return tuple(result+[('stop',)])

def policy_recipe(policy,registry,max_motion_calls=9):
    validate_physical_policy(policy,registry,max_motion_calls=max_motion_calls)
    result=[]
    for line in ast.parse(policy).body:
        call=line.value;name=call.func.attr
        if name=='stop': result.append(('stop',));continue
        if name not in ('move','reverse','turn'): return None
        item=registry.resolve_public_path((name,))
        values=item.spec.bind(tuple(call.args),{arg.arg:arg.value for arg in call.keywords})
        number=_literal_number(values['angle' if name=='turn' else 'distance'])
        result.append((name,{'move':move_mm,'reverse':reverse_mm,'turn':turn_tenths}[name](number)))
    return tuple(result)

def compatibility(registry,scope):
    return {'contract':CONTRACT,'scope':scope,
            'api':[asdict(spec) for spec in sorted(registry.list_visible(),key=lambda s:s.canonical_id)],
            'move_mm':[50,300],'turn_tenths':[150,900],'front_guard':'OFF',
            'execution_profile': registry.execution_profile()}

def _digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

class PhysicalPolicyLibrary:
    def __init__(self,path,*,scope='hardware'):
        if scope not in ('hardware','demo'): raise ValueError('Unknown policy library scope')
        self.path=Path(path);self.scope=scope;self.lock=threading.RLock()
        self.hits=self.misses=0;self.last_error=None
        self._file_stamp=None;self._loaded={}

    def _read(self):
        try:
            info=self.path.stat();stamp=(info.st_mtime_ns,info.st_size)
            if stamp==self._file_stamp:return dict(self._loaded)
            if info.st_size>1024*1024:return {}
            value=json.loads(self.path.read_text(encoding='utf-8'))
            if value.get('schema')!=1 or not isinstance(value.get('entries'),dict): return {}
            self._loaded=dict(list(value['entries'].items())[-MAX_ENTRIES:]);self._file_stamp=stamp
            return dict(self._loaded)
        except (OSError,ValueError,AttributeError): return {}

    def _write(self,entries):
        temporary=self.path.with_name(self.path.name+'.'+uuid4().hex+'.tmp')
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temporary.write_text(json.dumps({'schema':1,'entries':dict(list(entries.items())[-MAX_ENTRIES:])},ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(self.path);self.last_error=None;self._file_stamp=None
        except OSError as exc:
            self.last_error=type(exc).__name__  # A cache write never changes execution success.
        finally:
            try: temporary.unlink(missing_ok=True)
            except OSError: pass

    def lookup(self,instruction,registry,*,max_motion_calls=9,track=True):
        recipe=explicit_recipe(instruction)
        with self.lock:
            if recipe is None:
                self.misses+=int(track);return None,'context_or_unrecognized_request'
            contract=compatibility(registry,self.scope)
            key=_digest({'recipe':recipe,'compatibility':contract})
            entry=self._read().get(key)
            try:
                if (not isinstance(entry,dict) or entry.get('compatibility')!=json.loads(json.dumps(contract)) or
                    entry.get('verification')!='completed_feedback_not_ground_truth' or
                    not re.fullmatch(r'[a-f0-9]{32}',str(entry.get('source_episode_id',''))) or
                    type(entry.get('verified_runs')) is not int or entry['verified_runs']<1 or
                    entry.get('policy_sha256')!=_digest(entry.get('policy')) or
                    policy_recipe(entry['policy'],registry,max_motion_calls)!=recipe):
                    raise ValueError('Not a valid compatible recipe')
            except Exception:
                self.misses+=int(track);return None,'not_verified_or_incompatible'
            self.hits+=int(track)
            from copy import deepcopy
            return {**deepcopy(entry),'entry_id':key},'verified_match'

    def record(self,episode,registry):
        """Learn only fully completed, measured, stopped runs with matching intent."""
        recipe=explicit_recipe(episode.instruction)
        try:
            if (recipe is None or not episode.execution_success or episode.task_success is False or episode.error_type or
                any(event.get('phase') in ('failed','cancelled') for event in episode.trace) or
                episode.metrics.get('scope')!=self.scope or episode.api_surface_signature!=registry.api_surface_signature() or
                any(episode.final_state.get(k)!=v for k,v in {'connected':True,'ready':True,'stopped':True,'fault':None}.items()) or
                policy_recipe(episode.policy,registry)!=recipe): return None
            summary=_summary({'execution_success':True,'trace':episode.trace})
            if summary['status']!='completed': return None
            measured=[]
            for action in summary['completed_actions']:
                name=action['name']
                if name=='stop': continue  # Segment STOPs do not change the requested motion sequence.
                if name not in ('move','reverse','turn'): return None
                number=action['angle_deg'] if name=='turn' else action['distance_m']
                measured.append((name,{'move':move_mm,'reverse':reverse_mm,'turn':turn_tenths}[name](number)))
            if tuple(measured+[('stop',)])!=recipe: return None
        except Exception: return None
        contract=compatibility(registry,self.scope)
        profile=contract['execution_profile']
        if profile and any(state.get('execution_profile') != profile
                           for state in (episode.initial_state, episode.final_state)):
            return None  # Never learn a run across a configuration change.
        key=_digest({'recipe':recipe,'compatibility':contract})
        with self.lock:
            entries=self._read();old=entries.pop(key,{})
            if not isinstance(old,dict):old={}
            verified_runs=old.get('verified_runs',0)
            if type(verified_runs) is not int or verified_runs<0:verified_runs=0
            entries[key]={'policy':episode.policy,'policy_sha256':_digest(episode.policy),'compatibility':contract,
                          'source_episode_id':episode.episode_id,'verification':'completed_feedback_not_ground_truth',
                          'verified_runs':verified_runs+1}
            self._write(entries)
        return key

    def invalidate(self,instruction,registry):
        recipe=explicit_recipe(instruction)
        if recipe is None:return
        key=_digest({'recipe':recipe,'compatibility':compatibility(registry,self.scope)})
        with self.lock:
            entries=self._read()
            if key in entries: del entries[key];self._write(entries)

    def snapshot(self):
        with self.lock:
            return {'verified_recipes':len(self._read()),'hits':self.hits,'misses':self.misses,
                    'scope':self.scope,'last_error':self.last_error}

    def clear(self):
        with self.lock:self._write({})


def default_library_path(scope="hardware"):
    return Path(__file__).resolve().parents[1]/"runs"/"policy-library"/(scope+".json")
