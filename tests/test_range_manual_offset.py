"""Manual ruler offsets share the existing model observations/approach correction."""
import json
import time
import pytest
from webui.controller import ControlDesk
from test_webui import CLIENT, connect

def test_ruler_offset_shared_persisted_invalidates_preview(tmp_path):
    desk=ControlDesk(demo=True,log_dir=tmp_path,monitor=False)
    try:
        connect(desk)
        desk.plan={'id':'stale-range'}
        desk.set_range_offset(CLIENT,-20)
        assert desk.backend.get_distance()['distance_mm']==580
        assert desk.plan is None
        saved=json.loads((tmp_path/'range-calibration.json').read_text())
        assert saved['offset_mm']==-20 and saved['source']=='ruler_manual'
        restored=ControlDesk(demo=True,log_dir=tmp_path,monitor=False)
        try:
            assert restored.range_offset_mm==-20
            connect(restored)
            assert restored.backend.get_distance()['distance_mm']==580
        finally: restored.close()
    finally: desk.close()

@pytest.mark.parametrize('value',[True,-101,101,0.5,'-20',None])
def test_offset_validation_has_no_side_effects(tmp_path,value):
    desk=ControlDesk(demo=True,log_dir=tmp_path,monitor=False)
    try:
        with pytest.raises(ValueError): desk.set_range_offset(CLIENT,value)
        assert desk.range_offset_mm==0 and not (tmp_path/'range-calibration.json').exists()
    finally: desk.close()

def test_motion_in_progress_cannot_change_offset(tmp_path):
    desk=ControlDesk(demo=True,log_dir=tmp_path,monitor=False)
    try:
        connect(desk)
        desk.teleop_start(CLIENT,'forward')
        with pytest.raises(ValueError): desk.set_range_offset(CLIENT,-20)
        assert desk.range_offset_mm==0
    finally: desk.close()

def test_nonpositive_corrected_range_does_not_render_as_valid(tmp_path):
    desk=ControlDesk(demo=True,log_dir=tmp_path,monitor=False)
    try:
        connect(desk)
        desk.set_range_offset(CLIENT,-20)
        desk.telemetry['range']={'distance_mm':10,'ready':1,'valid':1,'status':0,'error':0,'age_ms':0,'_at':time.monotonic()}
        sample=desk.snapshot()['telemetry']['range']
        assert sample['raw_mm']==10 and sample['distance_mm']==-10 and not sample['fresh']
    finally: desk.close()
