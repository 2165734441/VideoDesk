import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from integrations.material_packages import health,nodes_from,merge_packages,safe,Materials,UPLOAD,EXTRA,CONTAINER,MergeRequest,Selection
from integrations.ai_package import report


class MaterialPackages(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)

    def package(self,name='样片',left=2,right=7,warnings=None):
        p=self.root/name;(p/UPLOAD).mkdir(parents=True);(p/EXTRA).mkdir()
        (p/UPLOAD/'原尺寸.png').write_bytes(b'original-pixels\x00'+name.encode())
        (p/UPLOAD/'01_完整内容_001.txt').write_text('价格199\n完整台词末尾不能丢\n'+name,encoding='utf-8')
        (p/UPLOAD/'00_阅读说明.txt').write_text('此来源阅读说明',encoding='utf-8')
        files=['原尺寸.png','01_完整内容_001.txt','00_阅读说明.txt']
        report(p,name,files,warnings or [],{})
        if left is not None:
            (p/EXTRA/'联合内容源.json').write_text(json.dumps({'title':name,'from':left,'to':right,'rows':[{'from':left+1,'to':left+2,'text':'价格199'}],'visuals':[],'images':['原尺寸.png'],'timeline':[]}),encoding='utf-8')
        return p

    def entry(self,p,**kw):
        nodes,upload=nodes_from(p);return {'nodes':nodes,'upload':upload,**kw}

    def test_warning_is_separate_from_file_integrity(self):
        p=self.package(warnings=['F00001 画面文字置信度偏低，请核对必要细节图'])
        state=health(p);self.assertTrue(state['available']);self.assertEqual(state['review_count'],1);self.assertFalse(state['content_missing'])
        self.assertEqual(read(p/EXTRA/'资料索引.json')['version'],2)

    def test_missing_files_are_explicit_and_cannot_merge(self):
        p=self.package();(p/UPLOAD/'原尺寸.png').unlink()
        self.assertEqual(health(p)['missing'],['原尺寸.png'])
        with self.assertRaises(ValueError):nodes_from(p)

    def test_audio_transcript_gap_is_content_issue(self):
        p=self.package(warnings=['有音轨但没有可用台词；需补充字幕'])
        self.assertTrue(health(p)['content_missing']);self.assertTrue(health(p)['available'])

    def test_all_files_unchanged_with_contiguous_clocks_and_episodes(self):
        a=self.package('中文 空格一');b=self.package('第二集')
        before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for f in [a,b] for p in (f/UPLOAD).iterdir()}
        target=self.root/'合集';merge_packages(target,[self.entry(a,episode=3,episode_title='第一课'),self.entry(b,episode=4)])
        data=read(target/EXTRA/CONTAINER);idx=read(target/EXTRA/'视频顺序与时间轴.json')
        self.assertEqual([i['from'] for i in idx],[0,5]);self.assertEqual([i['to'] for i in idx],[5,10]);self.assertEqual(idx[0]['episode']['number'],3)
        for node,source in zip(data['nodes'],[a,b]):
            for f in node['files']:self.assertEqual((target/UPLOAD/f['path']).read_bytes(),(source/UPLOAD/f['name']).read_bytes())
        self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for f in [a,b] for p in (f/UPLOAD).iterdir()})
        self.assertTrue(health(target)['available'])

    def test_remerging_joint_flattens_sources_and_preserves_episodes(self):
        a=self.package('one');b=self.package('two');c=self.package('three')
        first=self.root/'first';merge_packages(first,[self.entry(a,episode=5),self.entry(b,episode=6)])
        second=self.root/'second';merge_packages(second,[self.entry(first),self.entry(c)])
        data=read(second/EXTRA/CONTAINER)
        self.assertEqual(len(data['nodes']),3);self.assertEqual([n['episode'] for n in data['nodes'][:2]],[5,6])
        self.assertEqual(len([f for n in data['nodes'] for f in n['files']]),9)
        self.assertEqual(read(second/EXTRA/'视频顺序与时间轴.json')[-1]['from'],10)

    def test_upload_only_original_reading_detects_missing_listed_file(self):
        p=self.package(left=None);(p/EXTRA/'资料索引.json').unlink()
        (p/UPLOAD/'00_阅读说明.txt').write_text('列表如下：\nmissing.png\n01_完整内容_001.txt\n',encoding='utf-8')
        self.assertEqual(health(p/UPLOAD)['missing'],['missing.png'])

    def test_opaque_legacy_keeps_every_file_and_unknown_clock_propagates(self):
        a=self.package('unknown',left=None);b=self.package('known')
        target=self.root/'joint';merge_packages(target,[self.entry(a),self.entry(b)])
        timeline=read(target/EXTRA/'视频顺序与时间轴.json')
        self.assertIsNone(timeline[0]['to']);self.assertIsNone(timeline[1]['from'])
        self.assertEqual((target/UPLOAD/'V001/01_完整内容_001.txt').read_bytes(),(a/UPLOAD/'01_完整内容_001.txt').read_bytes())

    def test_legacy_joint_internal_boundaries_preserved(self):
        a=self.package('oldjoint',left=None)
        segments=[{'video':'V01','title':'甲','from':0,'to':5,'episode':{'number':1,'title':'第一集'}},{'video':'V02','title':'乙','from':5,'to':8}]
        (a/EXTRA/'视频顺序与时间轴.json').write_text(json.dumps(segments),encoding='utf-8')
        target=self.root/'joint';merge_packages(target,[self.entry(a)])
        idx=read(target/EXTRA/'视频顺序与时间轴.json');self.assertEqual(idx[0]['to'],8);self.assertEqual(idx[0]['segments'],segments)

    def test_duplicate_origins_are_warned_and_both_kept(self):
        p=self.package();target=self.root/'joint';result=merge_packages(target,[self.entry(p),self.entry(p)])
        self.assertEqual(result['metrics']['videos'],2);self.assertTrue(any('疑似' in w for w in result['warnings']))

    def test_discovery_avoids_nested_double_selection_and_skips_junctions(self):
        a=self.package('a');b=self.package('b')
        svc=Materials(SimpleNamespace(DATA=self.root,load_json=lambda p,d:d))
        roots,_=svc.discover([str(self.root),str(a/UPLOAD)])
        self.assertEqual(set(roots),{a,b});self.assertEqual(len(roots),2)

    def test_safety_rejects_paths_escaping_source(self):
        for name in ['../secrets','/absolute']:
            with self.assertRaises(ValueError):safe(self.root,name)

    def test_cancellation_checks_during_copy(self):
        p=self.package();calls=0
        def cancel():
            nonlocal calls
            calls+=1
            if calls>4:raise RuntimeError('cancelled')
        with self.assertRaisesRegex(RuntimeError,'cancelled'):merge_packages(self.root/'joint',[self.entry(p)],cancel)
        self.assertEqual((p/UPLOAD/'原尺寸.png').read_bytes(),b'original-pixels\x00'+p.name.encode())

    def test_added_files_in_generated_joint_are_not_dropped(self):
        p=self.package();first=self.root/'first';merge_packages(first,[self.entry(p)])
        (first/UPLOAD/'补充.txt').write_text('额外信息',encoding='utf-8')
        second=self.root/'second';merge_packages(second,[self.entry(first)])
        self.assertTrue(any(p.read_text('utf-8')=='额外信息' for p in (second/UPLOAD).rglob('补充.txt')))

    def test_container_missing_file_is_found_even_without_report(self):
        p=self.package();target=self.root/'joint';merge_packages(target,[self.entry(p)])
        (target/EXTRA/'资料索引.json').unlink();(target/UPLOAD/'V001/原尺寸.png').unlink()
        self.assertIn('V001/原尺寸.png',health(target)['missing'])

    def test_invalid_index_has_readable_error(self):
        p=self.package();(p/EXTRA/'资料索引.json').write_text('[]',encoding='utf-8')
        # Empty invalid JSON types must never silently become a legacy folder.
        (p/EXTRA/'资料索引.json').write_text('[1]',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'格式不正确'):health(p)

    def test_task_cancellation_removes_partial_stage_not_inputs(self):
        p=self.package();empty=self.root/'cancelled';empty.mkdir();data=self.root/'data';data.mkdir();calls=0
        def check(jid):
            nonlocal calls
            calls+=1
            if calls>5:raise RuntimeError('cancelled')
        a=SimpleNamespace(DATA=data,load_json=lambda p,d:d,job_by_id=lambda j:{'directory':str(empty)},check_cancel=check,change=lambda *args,**kwargs:None)
        service=Materials(a);key=service.register(p)
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            service.run('job',MergeRequest(items=[Selection(id=key)],request_id='cancel-test'),[Selection(id=key)])
        self.assertEqual(list(empty.iterdir()),[]);self.assertTrue((p/UPLOAD/'原尺寸.png').is_file())

def read(p):return json.loads(p.read_text('utf-8'))

if __name__=='__main__':unittest.main()
