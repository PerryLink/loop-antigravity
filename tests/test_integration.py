"""loop-antigravity integration tests."""
import pytest
from loop_antigravity.config import Config
from loop_antigravity.circuit_breaker import CircuitBreaker
from loop_antigravity.phase_dispatcher import PhaseDispatcher,Phase,Severity
from loop_antigravity.multimodal_handler import MultimodalHandler,MediaType,MediaInput as MI
@pytest.mark.integration
class TestConfigIntegration:
 def test_roundtrip(self,config_auto,state_manager,sample_state):
  sample_state["config"]=config_auto.to_dict();state_manager.write(sample_state)
  assert Config.from_dict(state_manager.read().data["config"]).mode=="auto"
 def test_billing_auto(self,config_auto):assert config_auto.billing.daily_cap_usd==20.0
 def test_billing_safe(self,config_safe):assert config_safe.billing.daily_cap_usd==5.0
 def test_cb(self,config_auto):
  assert CircuitBreaker.for_mode(config_auto.mode).failure_threshold==config_auto.failure_threshold
@pytest.mark.integration
class TestPhaseDispatcherIntegration:
 def test_part2(self):
  assert PhaseDispatcher().dispatch([],Phase.PART_1_1,2,{}).target_phase==Phase.PART_2_1
 def test_p0(self):
  r=PhaseDispatcher().dispatch([{"severity":"P0"}],Phase.PART_2_3,0,{})
  assert r.severity==Severity.P0_RED
 def test_p2(self):
  assert PhaseDispatcher().dispatch([],Phase.PART_1_3,0,{}).target_phase==Phase.PART_2_1
 def test_p1(self):
  r=PhaseDispatcher(2).dispatch([{"severity":"P1"}],Phase.PART_1_3,2,{})
  assert r.target_phase in(Phase.PART_2_1,Phase.PART_1_1)
 def test_budget(self,config_auto):
  from loop_antigravity.billing_tracker import BillingTracker as BT
  bt=BT(config_auto.mode);[bt.record(1000,500)for _ in range(3)]
  assert bt.get_daily_window().invocation_count==3
@pytest.mark.integration
class TestMultimodalIntegration:
 def test_process(self,tmp_path):
  p=tmp_path/"x.png";p.write_bytes(b"\x89PNG\r\n\x1a\n"+b"\x00"*100)
  r=MultimodalHandler(5).process([str(p)])
  assert len(r)==1 and"base64"in r[0]
 def test_gemini(self):
  mi=MI("x.png",MediaType.IMAGE,"PNG",100,"image/png","a")
  assert"inline_data"in MultimodalHandler().to_gemini_format(mi)
 def test_types(self):h=MultimodalHandler(5);assert h.detect_type("a.png")=="PNG"
 def test_size(self,tmp_path):
  p=tmp_path/"big.png";p.write_bytes(b"\x89PNG\r\n\x1a\n"+b"\x00"*(2*1024*1024))
  r=MultimodalHandler(1).process([str(p)])
  assert len(r)==1 and"warning"in r[0]
@pytest.mark.integration
class TestStateAndConfigIntegration:
 def test_mode(self,state_manager_with_data,config_auto):
  assert state_manager_with_data.read().data["config"]["mode"]=="auto"
 def test_write(self,config_auto,state_manager,sample_state):
  sample_state["config"]=config_auto.to_dict();state_manager.write(sample_state)
  assert all(k in state_manager.read().data["config"]for k in("mode","model","timeout_ms","daily_cap_usd"))
 def test_relect(self,state_manager_with_data,config_safe):
  r=state_manager_with_data.read();r.data["config"]["mode"]="safe"
  state_manager_with_data.write(r.data)
  assert state_manager_with_data.read().data["config"]["mode"]=="safe"
@pytest.mark.integration
class TestFullPipeline:
 def test_single(self,config_auto,state_manager_with_data,sample_state):
  sample_state["config"]=config_auto.to_dict()
  sample_state["progress"]["phase"]="mvp_agy_invoke"
  state_manager_with_data.write(sample_state)
  r=state_manager_with_data.read();r.data["progress"]["phase"]="mvp_complete"
  r.data["mvp_result"]["status"]="passed";state_manager_with_data.write(r.data)
  assert state_manager_with_data.read().data["mvp_result"]["status"]=="passed"
 def test_multi(self,state_manager,sample_state):
  sample_state["progress"]["cycle"]=3;state_manager.write(sample_state)
  assert state_manager.read().data["progress"]["cycle"]==3
 def test_p0(self):
  r=PhaseDispatcher().dispatch([{"severity":"P0"}],Phase.PART_1_1,0,{})
  assert r.severity==Severity.P0_RED
 def test_term(self):
  r=PhaseDispatcher(1).dispatch([],Phase.PART_2_8,1,{})
  assert r.should_terminate or r.target_phase==Phase.PART_2_8
