"""边缘测试"""
import os,pytest
from loop_antigravity.agy_client import MediaInput,_infer_media_type
from loop_antigravity.config import Config
from loop_antigravity.context_packer import ContextPacker
class TestPhaseDispatcherEdgeCases:
    """PhaseDispatcher"""
    @pytest.mark.parametrize("i,e",[([],0),([{"id":1,"s":"UNKNOWN"}],"UNKNOWN")])
    def test_issues_edge(self,state_manager,sample_state,i,e):
        """issues边界"""
        state_manager.write({**sample_state,"issues":i},validate=False)
        r=state_manager.read().data.get("issues",[])
        assert len(r)==e if isinstance(e,int)else r[0]["s"]==e
    def test_max_convergence(self,state_manager,sample_state):
        """收敛"""
        s={**sample_state};s["progress"]["convergence_counter"]=999
        state_manager.write(s,validate=False)
        assert state_manager.read().data["progress"]["convergence_counter"]==999
    def test_phase_no_backward(self,state_manager,sample_state):
        """回退"""
        s={**sample_state};s["progress"]["phase"]="invalid_backward"
        with pytest.raises(Exception):
            state_manager.validate(s)
    def test_large_issues(self,state_manager,sample_state):
        """100+issues"""
        state_manager.write({**sample_state,"issues":[{str(i):i}for i in range(101)]},validate=False)
        assert len(state_manager.read().data["issues"])==101
    def test_phase_stays(self,state_manager,sample_state):
        """不变"""
        state_manager.write(sample_state);r=state_manager.read()
        state_manager.write(sample_state)
        assert r.data["progress"]["phase"]==state_manager.read().data["progress"]["phase"]
class TestMultimodalHandlerEdgeCases:
    """MultimodalHandler"""
    def test_empty_files_raises(self,temp_state_dir):
        """空列表"""
        with pytest.raises(ValueError,match="selective"):
            ContextPacker().pack(temp_state_dir,strategy="selective",file_list=[])
    def test_20mb_limit(self)->None:
        """20MB"""
        assert not MediaInput(path="/f/v.mp4",mime_type="video/mp4",size_bytes=20*1024*1024).use_file_api
    def test_zero_bytes(self)->None:
        """零字节"""
        m=MediaInput(path="/f/e.png",mime_type="image/png",size_bytes=0)
        assert m.size_bytes==0 and not m.use_file_api
    @pytest.mark.parametrize("m,t",[("application/octet-stream","unknown"),("image/x-unknown","image"),("application/pdf","pdf")])
    def test_media_boundary(self,m,t):
        """MIME"""
        assert _infer_media_type(m)==t
    def test_png_ext_pdf(self)->None:
        """png PDF"""
        assert MediaInput(path="/f.png",mime_type="application/pdf").media_type=="pdf"
    def test_many_files(self,temp_state_dir):
        """110文件"""
        p=ContextPacker()
        fs=[os.path.join(temp_state_dir,f"f{i}.txt")for i in range(110)]
        for f in fs:
            open(f,"w").write("x")
        assert len(p.pack(temp_state_dir,strategy="selective",file_list=fs).files_included)>0
class TestCrossModuleEdgeCases:
    """跨模块"""
    @pytest.mark.parametrize("d",[{"config":{"uk":"v"}},{"pc":True}])
    def test_cross_config(self,state_manager,sample_state,d):
        """配置边界"""
        s={**sample_state}
        if"config"in d:
            s["config"]={**s["config"],**d["config"]}
        if"pc"in d:
            s["pc"]=d["pc"]
        state_manager.write(s,validate=False)
        r=state_manager.read().data
        assert r["config"]["mode"]=="auto"or r.get("pc")
    def test_convergence_boundary(self,state_manager,sample_state):
        """收敛边界"""
        sm=state_manager
        for v in(0,1,2147483647):
            s={**sample_state};s["progress"]["convergence_counter"]=v
            sm.write(s,validate=False)
            assert sm.read().data["progress"]["convergence_counter"]==v
    def test_extreme_max_cycles(self,config_auto):
        """max_cycles"""
        assert Config(mode="auto",max_cycles=100_000).runtime.max_cycles==100_000
