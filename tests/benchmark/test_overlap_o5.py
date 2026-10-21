#!/usr/bin/env python3
import unittest
import tempfile,json,hashlib
from pathlib import Path
from analyze_overlap_o5 import paired_ratio, distribution,trial_locations
from run_overlap_o5 import validate_schedule, controlled_environment
from profile_overlap_o5 import union,intersect,duration,halo_post_groups
from o5_rank_output import merge_rank_output
from observe_o5_gpu_mapping import window_observations

class StatisticsTests(unittest.TestCase):
    def test_halo_callbacks_are_not_directional_pack_count(self):
        posts=[dict(start=i,tag=(1101,1102,1102,1101)[i%4]) for i in range(12)]
        self.assertEqual(len(halo_post_groups(posts)),3)
        with self.assertRaises(ValueError):halo_post_groups(posts[:-1])
        posts[0]['tag']=1102
        with self.assertRaises(ValueError):halo_post_groups(posts)
    def test_independent_rank_output_preserves_original_and_rejects_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'stdout.txt').write_text('interleaved original\n')
            for rank in range(2):
                p=root/'mpioutput/1'/f'rank.{rank}'/'stdout';p.parent.mkdir(parents=True);p.write_text(f'rank={rank} complete\n')
            with self.assertRaises(ValueError):merge_rank_output(root,3)
            proof=merge_rank_output(root,2)
            self.assertEqual((root/'stdout.merged.txt').read_text(),'interleaved original\n')
            self.assertEqual((root/'stdout.txt').read_text(),'rank=0 complete\nrank=1 complete\n')
            self.assertEqual(proof['order'],[0,1])
    def test_prrte_rank_output_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'stdout.txt').write_text('original\n')
            for rank in range(2):(root/f'mpioutput.prterun-host-123@1.{rank}.out').write_text(f'rank={rank}\n')
            merge_rank_output(root,2)
            self.assertEqual((root/'stdout.txt').read_text(),'rank=0\nrank=1\n')
    def test_direct_rank_output_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'stdout.txt').write_text('launcher\n');(root/'rank-output').mkdir()
            for rank in range(2):(root/'rank-output'/f'rank-{rank}.stdout').write_text(f'rank={rank}\n')
            merge_rank_output(root,2)
            self.assertEqual((root/'stdout.txt').read_text(),'rank=0\nrank=1\n')
    def test_gpu_mapping_requires_complete_window_and_process_identity(self):
        row=dict(directory='/trial',rank=1,pid=20,start_ticks=30,identity_stable=True,gpu_uuids=['GPU-a'])
        samples=[dict(clock_domain_id='host',t_begin_ns=a,t_end_ns=z,ranks=[dict(row,**change)]) for a,z,change in
                 [(10,20,{}),(20,30,{}),(5,15,{}),(30,40,dict(start_ticks=31)),(20,25,dict(identity_stable=False))]]
        got=window_observations(samples,'/trial',1,20,30,'host',10,30)
        self.assertEqual([r['index'] for r in got],[0,1])
        self.assertEqual(window_observations(samples,'/trial',1,20,30,'other-clock',10,30),[])
    def test_resume_preserves_original_and_rejects_config_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);job=dict(id='original',steps=100,repeat=1,mode='full')
            plan=dict(identity={'binary':'fixed'},jobs=[job]);f=root/'frozen-plan.json';f.write_text(json.dumps(plan))
            resumed=dict(frozen_plan_sha256=hashlib.sha256(f.read_bytes()).hexdigest(),identity=plan['identity'],
                jobs=[dict(logical_id='original',group='continued',job=dict(job,id='attempt2'))])
            p=root/'continuation-plan.json';p.write_text(json.dumps(resumed))
            self.assertEqual(next(trial_locations(root,plan,[job]))[2],root/'continued/attempt2/result.json')
            resumed['jobs'][0]['job']['steps']=101;p.write_text(json.dumps(resumed))
            with self.assertRaises(ValueError):list(trial_locations(root,plan,[job]))
            resumed['jobs'][0]['job']['steps']=100;resumed['jobs']*=2;p.write_text(json.dumps(resumed))
            with self.assertRaises(ValueError):list(trial_locations(root,plan,[job]))
    def test_environment_whitelist(self):
        env=controlled_environment(dict(PATH='/supported/bin',OMPI_MCA_pml='ucx',CUDA_LAUNCH_BLOCKING='1',
            DMGMD_DOMAIN_TIMING='1',DMGMD_FORCE_POISON='1',LD_PRELOAD='unexpected.so',SECRET_TOKEN='secret'))
        self.assertEqual(env,dict(PATH='/supported/bin',OMPI_MCA_pml='ucx'))
    def test_real_interval_union_not_envelope(self):
        kernels=union([[0,2],[5,7],[6,8]])
        requests=union([[1,6],[4,7]])
        self.assertEqual(kernels,[[0,2],[5,8]])
        self.assertEqual(duration(intersect(kernels,requests)),3)
        self.assertEqual(duration(intersect([],requests)),0)
    def test_preserves_pairing(self):
        a={i:v for i,v in enumerate([2,4,8,16,32])}
        b={i:v/2 for i,v in a.items()}
        r=paired_ratio(a,b,resamples=1000)
        self.assertEqual(r['estimate'],2);self.assertEqual(r['ci95'],[2,2])
    def test_ratio_of_medians_not_median_of_ratios(self):
        r=paired_ratio(dict(enumerate([1,100,100])),dict(enumerate([1,10,100])))
        self.assertEqual(r['estimate'],10);self.assertIsNone(r['ci95'])
    def test_missing_blocks_and_unknown(self):
        r=paired_ratio({0:2,1:100},{0:1,2:1000})
        self.assertEqual(r['blocks'],[0]);self.assertEqual(r['estimate'],2)
        self.assertIsNone(paired_ratio({0:1},{1:1})['estimate'])
        self.assertIsNone(distribution([])['median'])
        zero=distribution([0,0,0])
        self.assertEqual(zero['median'],0);self.assertEqual(zero['mad'],0)
        self.assertIsNone(zero['relative_range'])
    def test_seed_reproducibility_and_dispersion(self):
        a=dict(enumerate([1,2,3,4,8]));b=dict(enumerate([2,2,3,3,3]))
        self.assertEqual(paired_ratio(a,b,resamples=500),paired_ratio(a,b,resamples=500))
        d=distribution([1,2,3,4,5]);self.assertEqual(d['median'],3);self.assertEqual(d['mad'],1)
    def test_full_fallback_can_poll_without_early(self):
        text='DMGMD_OVERLAP_CONFIG mode=poll\n'
        for rank in range(2):
            for seq in range(2):
                text+=f'DMGMD_DOMAIN_SUMMARY rank={rank} sequence={seq} steps=2 rebuild_steps=0\n'
                text+=f'DMGMD_OVERLAP_SUMMARY rank={rank} sequence={seq} cumulative_calls=0 cumulative_polls={(seq+1)*16}\n'
        r=validate_schedule(text,2,'poll')
        self.assertEqual(r['calls'],0);self.assertEqual(r['finish_callbacks'],8);self.assertEqual(r['polls'],64)
        with self.assertRaises(ValueError):validate_schedule(text.replace('cumulative_polls=32','cumulative_polls=33'),2,'poll')
        with self.assertRaises(ValueError):validate_schedule(text.replace('steps=2 rebuild_steps=0','steps=2 rebuild_steps=1'),2,'poll')

if __name__=='__main__':unittest.main()
