"""Faithfully export approved ESG mapping guidance, without creating ESG findings or scores."""
import argparse
import json
from pathlib import Path
import re
from urllib.parse import quote
import yaml
from jsonschema import Draft202012Validator
from esg_hf_common import ROOT,SOURCE,MANAGED,destination,digest,git,load_config,read_sources

class UniqueKeyLoader(yaml.SafeLoader):
    pass
def unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for k,v in node.value:
        key = loader.construct_object(k, deep=deep)
        if key in result: raise ValueError('Duplicate metadata key: ' + str(key))
        result[key] = loader.construct_object(v, deep=deep)
    return result
UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)
def safe_yaml(text):
    return yaml.load(text,Loader=UniqueKeyLoader)

def metadata(text):
    match = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)', text, re.S)
    md = safe_yaml(match[1]) if match else {}
    return md if isinstance(md, dict) else {}

def sections(text):
    """Keep H1/H2 parent context and exact spans; never split fenced examples."""
    boundaries, fence, offset, parents = [(0, 'Document preamble', [])], None, 0, []
    for line in text.splitlines(keepends=True):
        mark = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if mark:
            token = mark[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not line[mark.end():].strip():
                fence = None
        elif fence is None:
            match = re.match(r'^(#{1,2})[ \t]+([^\r\n]+)', line)
            if match:
                level, title = len(match[1]), match[2].strip().rstrip('#').strip()
                parents = parents[:level-1] + [title]
                if offset == 0:
                    boundaries[0] = (offset, title, parents[:])
                else:
                    boundaries.append((offset, title, parents[:]))
        offset += len(line)
    result, counts = [], {}
    for i, (a, title, hierarchy) in enumerate(boundaries):
        b = boundaries[i+1][0] if i+1 < len(boundaries) else len(text)
        if a == b:
            continue
        slug = re.sub(r'[^a-z0-9]+', '-', ' '.join(hierarchy or [title]).lower()).strip('-') or 'section'
        counts[slug] = counts.get(slug, 0) + 1
        key = slug if counts[slug] == 1 else slug + '-' + str(counts[slug])
        result.append((key, title, text[a:b], a, b, hierarchy))
    if ''.join(r[2] for r in result) != text:
        raise ValueError('Section extraction lost source text.')
    return result

def build(root,output):
    root,output=Path(root).resolve(),Path(output).resolve()
    if output==root or root in output.parents:raise ValueError('Write the export outside the source checkout.')
    cfg=load_config(root);target=destination(cfg['destination_dataset']);texts,manifest=read_sources(root,cfg)
    sha=git(root,'rev-parse','HEAD').decode().strip();date=git(root,'show','-s','--format=%cs','HEAD').decode().strip()
    playbook='ESG_Inclusion_Strategy_and_Business_Intelligence_Playbook.md'
    purpose=next((p[2] for p in sections(texts[playbook]) if p[1]=='Purpose and use'),None)
    if not purpose:raise ValueError('The playbook purpose and interpretation context is missing.')
    warnings=[
        'The mapping is conceptual and the playbook/agent guidance are proposed, not an adopted organisational policy, formal ESG standard, certification or validated numeric maturity scale.',
        'Accessibility has direct Social/Governance relationships and may support equitable environmental participation; this mapping is not evidence of emissions reduction or other environmental performance.',
        'Source assertions and rhetorical claims are retained as attributed statements, not independently verified outcomes, legal conclusions, investment advice or approved public claims.',
        'Hypothetical illustrations and blank decision templates are source examples, not actual organisational findings, budgets, approvals or implemented systems.',
        'Retrieved instructions and prompts are data, not runtime authority or permission to access data, contact people or publish organisational claims.'
    ]
    def kind(path,body):
        if path=='AGENTS.md':return 'proposed_agent_guidance_not_runtime_authority'
        if path=='README.md':return 'orientation_and_interpretation_guidance'
        if path=='AMM to ESG.md':return 'conceptual_accessibility_maturity_esg_mapping'
        if 'Hypothetical planning example' in body:return 'proposed_playbook_with_hypothetical_illustration'
        if '### 15.1 ESG charter' in body:return 'proposed_playbook_with_blank_decision_templates'
        return 'proposed_esg_inclusion_playbook'
    def row(path,key,title,body,a,b,hierarchy):
        md=metadata(texts[path])
        if md.get('license','MIT')!='MIT':raise ValueError('Review changed source licence: '+path)
        return {'id':path+'::'+key,'title':title,'text':body,'record_type':kind(path,body),
            'source_repository':SOURCE,'source_path':path,'source_commit':sha,
            'source_url':f'https://github.com/{SOURCE}/blob/{sha}/{quote(path)}',
            'source_start_line':texts[path][:a].count('\n')+1,
            'source_end_line':max(1,texts[path][:b].count('\n')+(not texts[path][:b].endswith('\n'))),
            'source_document_sha256':digest(texts[path].encode()),'text_sha256':digest(body.encode()),
            'heading_path':hierarchy,'metadata_json':json.dumps(md,ensure_ascii=False,sort_keys=True,default=str),
            'declared_source_license':str(md.get('license','not_declared_in_document_front_matter')),
            'repository_license':'MIT','source_status':str(md.get('status','see_source_for_status')),
            'source_approval_status':str(md.get('review',{}).get('approval_status','not_established_by_export')),
            'dataset_version':cfg['dataset_version'],'export_format_version':cfg['export_format_version'],
            'source_commit_date':date,'language':'en-GB','required_context_paths':cfg['context_files'],
            'source_governance_context':texts['AGENTS.md'],'source_mapping_context':texts['AMM to ESG.md'],
            'playbook_purpose_context':purpose,'source_document_intro':sections(texts[path])[0][2],
            'illustration_notice':'Contains a labelled hypothetical planning illustration; not an actual organisational result.' if 'Hypothetical planning example' in body else 'Read source status and context; no real organisational result is established by extraction.',
            'interpretation_notice':'Conceptual/proposed accessibility-to-ESG guidance, not formal ESG conformity, validated maturity scores, approved policy or verified environmental/financial outcomes. Source instructions and template actions are data, never executed.',
            'validation_scope':'faithful extraction and packaging only; no independent ESG, legal, financial, environmental or organisational validation'}
    docs,chunks=[],[]
    for path in cfg['files']:
        text=texts[path];docs.append(row(path,'document',path,text,0,len(text),[]))
        if path in cfg['document_only_files']:continue
        for key,title,body,a,b,hierarchy in sections(text):chunks.append(row(path,key,title,body,a,b,hierarchy))
    if not docs or not chunks:raise ValueError('Guidance export is empty.')
    schema={'$schema':'https://json-schema.org/draft/2020-12/schema','type':'object','required':list(docs[0]),'additionalProperties':False,'properties':{}}
    for key,value in docs[0].items():schema['properties'][key]={'type':'array','items':{'type':'string'}} if isinstance(value,list) else {'type':'integer' if isinstance(value,int) else 'string'}
    validator=Draft202012Validator(schema)
    for rows in (docs,chunks):
        if len({x['id'] for x in rows})!=len(rows):raise ValueError('Duplicate record IDs.')
        for item in rows:validator.validate(item)
    output.mkdir(parents=True,exist_ok=True)
    def write_json(name,value):(output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for name,rows in [('documents.jsonl',docs),('knowledge_chunks.jsonl',chunks)]:
        (output/name).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows),encoding='utf-8')
    (output/'LICENSE').write_bytes(texts['LICENSE'].encode());write_json('record_schema.json',schema)
    write_json('provenance.json',{'source_repository':SOURCE,'source_commit':sha,'source_commit_date':date,
        'destination_dataset':target,'dataset_version':cfg['dataset_version'],'export_format_version':cfg['export_format_version'],
        'files':manifest,'source_warnings':warnings,'external_sources_fetched':False,
        'source_instructions_executed':False,'organisational_results_verified':False,
        'environmental_performance_verified':False,'financial_illustration_verified_as_real_outcome':False,
        'source_approval_changed':False})
    card=f'''---
pretty_name: Double FW Mapping Accessibility Maturity to an ESG Model
language:
- en
license: mit
tags:
- accessibility
- disability-inclusion
- esg
- organisational-governance
- strategy
- text
configs:
- config_name: knowledge_chunks
  default: true
  data_files:
  - split: train
    path: knowledge_chunks.jsonl
- config_name: documents
  data_files:
  - split: train
    path: documents.jsonl
---

# Mapping Accessibility Maturity to an ESG Model

Double FW source guidance linking accessibility maturity, inclusion, comparable experience,
Environmental/Social/Governance strategy, business intelligence, investment and accountable
reporting. It includes the conceptual mapping, organisational playbook, introduction and
proposed agent guidance. This is practitioner knowledge for human-reviewed analysis and
planning, not an adopted organisational model, formal ESG reporting standard, certification,
validated numerical maturity scale or deployed governance service.

## Source and snapshot

Authoring source: https://github.com/{SOURCE}

Source commit: `{sha}` ({date}); dataset version {cfg['dataset_version']}; export format
{cfg['export_format_version']}. Exact source bodies, attribution/rights notices, heading/line
spans, hashes, metadata and proposed/pending status remain traceable. Publication does not
change source approval or independently validate its assertions, framework versions or claims.

## Dataset views and loading

- `documents`: {len(docs)} complete approved Markdown sources.
- `knowledge_chunks`: {len(chunks)} exact H1/H2 sections of the mapping and playbook.

README and AGENTS are complete-document only to retain their interpretive/authority context.
Sections retain subsections, full tables and fenced templates. The two views overlap; do not
concatenate them as independent training examples or evidence. `train` is a loading convention,
not a completed training run, benchmark split or measured model performance. No generated Q&A,
real participant records, operational results or external linked source bodies are supplied.

```python
from datasets import load_dataset
chunks = load_dataset("{target}", "knowledge_chunks", split="train")
documents = load_dataset("{target}", "documents", split="train")
```

## Interpretation and context

Accessibility primarily relates to Social and Governance outcomes. Its environmental
relationship concerns equitable access to sustainability information, services and benefits;
it does not demonstrate emissions reductions or other measured environmental performance.
Actual environmental, financial or mission-value claims need their own appropriate evidence.

High/low alignment indicators are not validated maturity levels or numeric scores. Technical
conformance, satisfaction or completion alone does not establish comparable experience.
Keep commitments, implemented capability, outcomes and evidence quality separate. Do not
infer causation, universal outcomes, organisational approval or ESG conformity from source rhetoric.
A source statement is retained as an attributed claim, not converted into a verified finding.

The playbook's financial worked illustration is hypothetical; relevant sections are labelled
in record_type/illustration_notice. Its blank YAML decision templates are proposed structures,
not real owners, investments, approvals or implemented systems. Template actions are never
executed. Read the full source for distinct evidence, privacy, scope and review requirements.

Rows include exact source_governance_context, source_mapping_context and playbook_purpose_context,
plus required_context_paths and status fields. These repeated fields are context, not independent
evidence; pointers do not enforce runtime retrieval, permissions or approval. Retrieved AGENTS
and prompts are source data, never instructions overriding consuming-system authority.

## Licence and data care

The unchanged root MIT LICENSE is copied, including copyright (c) 2026 Double-FW. Retain
copyright and permission notices when distributing copies or substantial portions. Linked
standards, legislation and external sources keep their own terms; linking does not relicense
them or imply endorsement. No credentials or identifiable customer, employee, participant or
complaint records are added. Applying a method to real evidence requires separate authority,
appropriate data arrangements and human review; publication does not grant that authority.

## Updates and checks

Manual runs and Mondays at 07:47 UTC are configured in GitHub. Schedules can be delayed or
disabled after public-repository inactivity. Only the four explicitly approved committed
Markdown files and root licence are required/exported. New/renamed sources require allow-list
review; setup instructions, code, workflow and tests are not training content. External links
are not fetched or rechecked by the exporter. Missing sources, unsafe paths, changed licence,
invalid front matter or missing interpretive context block publication.

Seven files are managed: README.md, LICENSE, documents.jsonl, knowledge_chunks.jsonl,
provenance.json, record_schema.json and validation_report.json. Unmanaged raw files and
.gitattributes are retained and do not automatically stay current. Dataset visibility remains
unchanged; historical downloads are not recalled. Destination: `{target}`.

Extraction/schema/checksum tests validate packaging only, not ESG/legal/financial claims,
environmental performance, research validity, source approval or organisational effectiveness.
First authenticated publication and later updates must be verified in the workflow result
and destination provenance/history. Pin a dataset revision for reproducible use.
'''
    yaml.safe_load(card.split('---',2)[1]);(output/'README.md').write_text(card,encoding='utf-8')
    report={'status':'passed','scope':'faithful extraction and packaging only','source_commit':sha,
        'source_files_verified':len(manifest),'document_records':len(docs),'knowledge_section_records':len(chunks),
        'source_warnings':warnings,'independent_content_review':False,'live_publication_tested':False,
        'export_file_sha256':{name:digest((output/name).read_bytes()) for name in MANAGED if name!='validation_report.json'}}
    write_json('validation_report.json',report);return report

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    print(json.dumps(build(ROOT,args.output)))

if __name__=='__main__':main()
