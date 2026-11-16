"""Committed-source export and mocked publication safeguards; never writes to a live dataset."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import esg_hf_common as common
import export_huggingface as exporter
import publish_huggingface as publisher

class ESGSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.repo=self.base/'repo';self.repo.mkdir()
        cfg=common.load_config(ROOT)
        for name in cfg['files']+['LICENSE','huggingface/export-config.json']:
            target=self.repo/name;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(common.git(ROOT,'show','HEAD:'+name))
        self.git('init','-q');self.git('config','user.name','Test fixture');self.git('config','user.email','test@example.invalid');self.commit()
        self.out=self.base/'export'
    def git(self,*args):return common.git(self.repo,*args)
    def commit(self):self.git('add','.');self.git('commit','--allow-empty','-qm','Fixture')
    def build(self):return exporter.build(self.repo,self.out)
    def rows(self,name):return [json.loads(x) for x in (self.out/name).read_text().splitlines()]
    def change_config(self,fn):
        p=self.repo/'huggingface/export-config.json';cfg=json.loads(p.read_text());fn(cfg);p.write_text(json.dumps(cfg));self.commit()

    def test_documents_and_sections_reproduce_exact_committed_bytes(self):
        self.build();cfg=common.load_config(self.repo)
        for row in self.rows('documents.jsonl'):
            self.assertEqual(row['text'].encode(),self.git('show','HEAD:'+row['source_path']))
            chunks=[x for x in self.rows('knowledge_chunks.jsonl') if x['source_path']==row['source_path']]
            self.assertEqual(''.join(x['text'] for x in chunks),'' if row['source_path'] in cfg['document_only_files'] else row['text'])
            self.assertEqual(row['text_sha256'],common.digest(row['text'].encode()))

    def test_context_status_hypothetical_and_template_labels_are_preserved(self):
        self.build();docs=self.rows('documents.jsonl');chunks=self.rows('knowledge_chunks.jsonl')
        agent=next(x for x in docs if x['source_path']=='AGENTS.md')
        self.assertEqual(agent['source_status'],'proposed');self.assertEqual(agent['source_approval_status'],'pending')
        self.assertTrue(any(x['record_type']=='proposed_playbook_with_hypothetical_illustration' for x in chunks))
        template=next(x for x in chunks if x['record_type']=='proposed_playbook_with_blank_decision_templates')
        self.assertIn('approval_reference: null',template['text'])
        for row in docs+chunks:
            self.assertEqual(row['source_governance_context'],(self.repo/'AGENTS.md').read_text())
            self.assertEqual(row['source_mapping_context'],(self.repo/'AMM to ESG.md').read_text())
            self.assertIn('Status and interpretation',row['playbook_purpose_context'])
            self.assertIn('not formal ESG conformity',row['interpretation_notice'])

    def test_all_four_sources_and_limits_survive(self):
        self.build();p=json.loads((self.out/'provenance.json').read_text())
        self.assertEqual(len(self.rows('documents.jsonl')),4)
        for key in ['source_instructions_executed','organisational_results_verified','environmental_performance_verified','financial_illustration_verified_as_real_outcome','source_approval_changed','external_sources_fetched']:
            self.assertFalse(p[key])
        card=(self.out/'README.md').read_text()
        self.assertIn('does not demonstrate emissions reductions',card)
        self.assertIn('validated numerical maturity scale',card)

    def test_instructions_and_orientation_are_not_fragmented(self):
        self.build()
        paths={r['source_path'] for r in self.rows('knowledge_chunks.jsonl')}
        self.assertNotIn('AGENTS.md',paths);self.assertNotIn('README.md',paths)
        self.assertEqual(paths,{'AMM to ESG.md','ESG_Inclusion_Strategy_and_Business_Intelligence_Playbook.md'})

    def test_metadata_and_root_licence_preserved(self):
        self.build();row=next(x for x in self.rows('documents.jsonl') if x['source_path']=='AGENTS.md')
        self.assertEqual(row['source_status'],'proposed');self.assertEqual(row['declared_source_license'],'MIT')
        self.assertEqual((self.out/'LICENSE').read_bytes(),self.git('show','HEAD:LICENSE'))

    def test_repeatable_output(self):
        self.build();before={p.name:p.read_bytes() for p in self.out.iterdir()}
        self.build();self.assertEqual(before,{p.name:p.read_bytes() for p in self.out.iterdir()})

    def test_new_unapproved_and_uncommitted_content_stays_out(self):
        (self.repo/'not-approved.md').write_text('Unapproved example');self.commit()
        p=self.repo/'README.md';original=p.read_bytes();p.write_text('Uncommitted edits')
        self.build();rows=self.rows('documents.jsonl')
        self.assertNotIn('not-approved.md',{x['source_path'] for x in rows})
        self.assertEqual(next(x for x in rows if x['source_path']=='README.md')['text'].encode(),original)
        cfg=self.repo/'huggingface/export-config.json';d=json.loads(cfg.read_text());d['destination_dataset']='Another/Dataset';cfg.write_text(json.dumps(d))
        self.build()  # Uncommitted config is ignored, just like uncommitted content.

    def test_missing_each_required_source_blocks(self):
        for path in common.load_config(self.repo)['required_files']:
            with self.subTest(path=path):
                p=self.repo/path;saved=p.read_bytes();p.unlink();self.commit()
                with self.assertRaises(ValueError):self.build()
                p.write_bytes(saved);self.commit()

    def test_changed_licence_or_nonregular_source_blocks(self):
        p=self.repo/'LICENSE';original=p.read_bytes();p.write_text('Changed terms');self.commit()
        with self.assertRaises(ValueError):self.build()
        p.write_bytes(original);self.commit()
        p=self.repo/'README.md';p.unlink();p.symlink_to('AGENTS.md');self.commit()
        with self.assertRaises(ValueError):self.build()

    def test_missing_purpose_or_invalid_frontmatter_blocks(self):
        p=self.repo/'ESG_Inclusion_Strategy_and_Business_Intelligence_Playbook.md';saved=p.read_text()
        p.write_text(saved.replace('## Purpose and use','## Removed purpose context',1));self.commit()
        with self.assertRaises(ValueError):self.build()
        p.write_text(saved);self.commit()
        p=self.repo/'AGENTS.md';saved=p.read_text()
        p.write_text(saved.replace('license: "MIT"','license: "Changed"',1));self.commit()
        with self.assertRaises(ValueError):self.build()

    def test_unsafe_config_and_missing_context_reject(self):
        for unsafe in ['../private.md','/private.md','.secrets.md']:
            self.change_config(lambda c:c['files'].append(unsafe))
            with self.assertRaises(ValueError):self.build()
            self.git('reset','--hard','HEAD~1')
        self.change_config(lambda c:c.update(context_files=[]))
        with self.assertRaises(ValueError):self.build()

    def test_wrong_source_destination_and_output_location_reject(self):
        for name in ['', 'GarethFW/accessible-chatbots','GarethFW/POUR_for_Branding_Systems_and_CX']:
            with self.assertRaises(ValueError):common.destination(name)
        self.change_config(lambda c:c.update(source_repository='Other/Repo'))
        with self.assertRaises(ValueError):self.build()
        with self.assertRaises(ValueError):exporter.build(self.repo,self.repo/'export')

    def test_fenced_headings_and_exact_spans(self):
        text='---\nstatus: draft\n---\n# Parent\n## One\n```md\n## Not a boundary\n```\n# Next\n## Two\nBody\n'
        parts=exporter.sections(text)
        self.assertEqual(''.join(x[2] for x in parts),text)
        self.assertEqual(len(parts),5);self.assertEqual(parts[-1][-1],['Next','Two'])
        self.assertIn('Not a boundary',parts[2][2])

    def test_yaml_frontmatter_duplicate_keys_and_unsafe_tags_reject(self):
        import yaml
        with self.assertRaises(ValueError):exporter.safe_yaml('a: 1\na: 2\n')
        with self.assertRaises(yaml.constructor.ConstructorError):exporter.safe_yaml('!!python/object/apply:os.system [forbidden]')

    def test_private_source_rejects(self):
        response=Mock();response.read.return_value=json.dumps({'full_name':common.SOURCE,'private':True,'visibility':'private'}).encode()
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        with patch.object(common.urllib.request,'urlopen',return_value=response):
            with self.assertRaises(ValueError):common.public_source()

    def test_missing_or_whitespace_token_rejects_without_network(self):
        self.build()
        for token in ['', 'fake-token\n',' fake-token','fake\ttoken']:
            with self.assertRaises(ValueError):publisher.publish(self.out,token,check_public=lambda:self.fail('No network expected'))

    def test_noop_and_first_commit_are_atomic_and_preserve_other_files(self):
        self.build();api=Mock();api.repo_info.return_value.sha='read-parent'
        same=lambda **kw:str(self.out/kw['filename'])
        self.assertIn('No changes',publisher.publish(self.out,'fake-test-token',api=api,download=same,check_public=lambda:None))
        api.create_commit.assert_not_called()
        from huggingface_hub.errors import EntryNotFoundError
        def empty(**kw):raise EntryNotFoundError('Missing fixture file')
        publisher.publish(self.out,'fake-test-token',api=api,download=empty,check_public=lambda:None)
        args=api.create_commit.call_args.kwargs
        self.assertEqual(args['repo_id'],common.TARGET);self.assertEqual(args['parent_commit'],'read-parent')
        self.assertEqual({x.path_in_repo for x in args['operations']},set(common.MANAGED))
        api.create_repo.assert_not_called();api.update_repo_settings.assert_not_called()

    def test_tampering_and_foreign_provenance_block(self):
        self.build();api=Mock();api.repo_info.return_value.sha='head'
        foreign=self.base/'foreign.json';foreign.write_text(json.dumps({'source_repository':'Other/Repo'}))
        def old(**kw):return str(foreign if kw['filename']=='provenance.json' else self.out/kw['filename'])
        with self.assertRaises(ValueError):publisher.publish(self.out,'fake',api=api,download=old,check_public=lambda:None)
        (self.out/'README.md').write_text('Tampered')
        with self.assertRaises(ValueError):publisher.publish(self.out,'fake',api=api,download=old,check_public=lambda:None)
        api.create_commit.assert_not_called()

    def test_export_destination_mismatch_blocks(self):
        self.build();p=self.out/'provenance.json';d=json.loads(p.read_text());d['destination_dataset']='Other/Dataset';p.write_text(json.dumps(d))
        api=Mock()
        with self.assertRaises(ValueError):publisher.publish(self.out,'fake',api=api,check_public=lambda:None)
        api.repo_info.assert_not_called()

    def test_change_report_ignores_revision_only_and_tracks_removal(self):
        delta=publisher.changes_between([{'id':'old','text':'x'},{'id':'same','text':'x','source_commit':'old'}],[{'id':'new','text':'y'},{'id':'same','text':'x','source_commit':'new'}])
        self.assertEqual(delta,{'added':['new'],'removed':['old'],'updated':[]})

    def test_workflow_uses_fixed_source_schedule_and_secret(self):
        import yaml
        w=yaml.load((ROOT/'.github/workflows/huggingface-sync.yml').read_text(),Loader=yaml.BaseLoader)
        self.assertEqual(w['on']['schedule'][0]['cron'],'47 7 * * 1')
        self.assertIn("github.repository == '"+common.SOURCE+"'",w['jobs']['publish']['if'])
        self.assertIn('workflow_dispatch',w['on'])
        self.assertEqual(w['permissions']['contents'],'read')
        self.assertEqual(w['jobs']['publish']['steps'][-1]['env']['HF_TOKEN'],'${{ secrets.HF_TOKEN }}')

if __name__=='__main__':unittest.main()
