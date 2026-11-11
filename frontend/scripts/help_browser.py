"""Isolated browser fixture. All API requests are intercepted; never calls a real backend."""
import copy
import json
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
QUESTION = 'What is the balance transfer fee for Apex Platinum?'
DOC = {'doc_id': 'DEMO-DOC', 'title': '演示数据 · Platinum 余额转移手续费核验',
       'card_tier': 'Apex Platinum', 'category': '演示政策', 'effective_year': 2026,
       'content': '演示数据 — 仅用于操作指南，不是真实政策。\nBalance transfer fee for Apex Platinum: consult the applicable policy for the percentage, minimum charge, eligibility and exceptions.\n本演示不提供实际费率；真实使用时，请核对当前原文中的费率、最低收费和适用条件。'}
TIMINGS = {'embedding': 50, 'dense': 2, 'sparse': 3, 'fusion': 1, 'rerank': 75,
           'retrieval_total': 135, 'recall_fusion': 5, 'generation': 80, 'answer_total': 215}
RETRIEVAL = {'request_id': 'DEMO-REQUEST', 'build_id': 'DEMO-POLICY', 'mode': 'hybrid_rerank',
             'results': [{**DOC, 'rank': 1, 'score': .87,
                          'evidence': [{'chunk_id': 'DEMO-CHUNK', 'text': DOC['content']}]}],
             'timings_ms': TIMINGS,
             'trace': {s: [{'chunk_id': 'DEMO-CHUNK', 'doc_id': DOC['doc_id'], 'rank': 1, 'score': n}]
                       for s, n in [('dense', .8), ('sparse', .04), ('fusion', .032), ('rerank', .87)]},
             'graph_paths': [], 'warnings': ['演示数据：仅验证交互，不代表真实政策、性能或成绩。']}
ANSWER = {'request_id': 'DEMO-ANSWER', 'answer': '## 演示数据 · 核验余额转移手续费\n请打开当前政策原文，核对 Apex Platinum 的费率、最低收费及适用条件。[1]\n\n本演示不提供实际收费结论。',
          'citations': [{'id': 1, 'doc_id': DOC['doc_id'], 'chunk_id': 'DEMO-CHUNK', 'quote': DOC['content']}],
          'retrieval': RETRIEVAL, 'timings_ms': TIMINGS, 'evidence_insufficient': False,
          'citation_valid': True, 'warnings': []}
METRICS = {f'{name}@{k}': n for name, n in [('recall', .75), ('precision', .4), ('ndcg', .7)] for k in [1, 3, 5]}
METRICS.update({'mrr@20': .8, 'counts': {'total': 30, 'succeeded': 30, 'failed': 0},
                'latency_ms': {'database': {'p50': 2, 'p99': 3, 'samples': 30},
                               'retrieval_total': {'p50': 135, 'p99': 180, 'samples': 30}}})
DETAIL = {'run_id': 'DEMO-RUN', 'status': 'completed',
          'config': {'strategy': 'policy_hierarchy', 'mode': 'hybrid_rerank', 'demo': '演示数据'},
          'metrics': METRICS, 'artifacts': {}, 'per_query': [{
              'query_id': 'DEMO-Q', 'query': QUESTION, 'relevant_doc_ids': ['DEMO-DOC'],
              'gold_response': '演示数据：核对原文中的费率与适用条件。', 'ranked_doc_ids': ['DEMO-DOC'],
              'metrics': METRICS, 'timings_ms': TIMINGS, 'error': None, 'retrieval': RETRIEVAL}]}
OPTIONS = {'builds': [{'build_id': b, 'strategy': s, 'documents': 150, 'chunks': 200, 'models': {}}
                      for b, s in [('DEMO-FIXED', 'fixed_window'), ('DEMO-POLICY', 'policy_hierarchy')]],
           'profiles': ['single', 'six'], 'modes': ['dense', 'hybrid', 'hybrid_rerank'],
           'candidate_depth': 20, 'rerank_depth': 20, 'top_k': 5, 'questions_per_run': 30,
           'limits': {'concurrent_jobs': 1, 'single_http_attempts': 180, 'six_http_attempts': 720,
                      'single_cost_cny': 3, 'six_cost_cny': 12, 'single_timeout_seconds': 900, 'six_timeout_seconds': 3600},
           'prices_cny_per_million': {'embedding': .5, 'rerank': .8},
           'conversation': {'max_messages': 6, 'max_message_chars': 4000, 'max_total_chars': 12000,
                            'context_mode': 'bounded_history_rewrite', 'history_is_policy_evidence': False}}


def estimate(body):
    six = body.get('profile') == 'six'
    configs = ([{'build_id': b, 'strategy': s, 'mode': m} for s, b in body['build_ids'].items()
                for m in OPTIONS['modes']] if six else [{'build_id': body['build_id'], 'mode': body['mode']}])
    return {'configurations': configs, 'total_questions': 180 if six else 30, 'logical_calls_max': 240 if six else 60,
            'http_attempts_max': 720 if six else 180, 'cost_ceiling_cny': 12 if six else 3,
            'conservative_estimate_cny': 5 if six else .1, 'retries_max': 2, 'candidate_depth': 20,
            'rerank_depth': 20, 'top_k': 5, 'query_cache': False,
            'prices_cny_per_million': OPTIONS['prices_cny_per_million'], 'caveats': ['演示数据：估算不是实际账单。']}


def install_demo(context):
    state = {'calls': [], 'jobs': [], 'fail_answer': False}

    def handle(route):
        req = route.request
        url = urlparse(req.url)
        pathname = url.path
        if not pathname.startswith('/api/'):
            if url.hostname not in ('127.0.0.1', 'localhost'):
                return route.abort()
            return route.continue_()
        body = req.post_data_json if req.post_data else {}
        state['calls'].append({'path': pathname, 'method': req.method, 'body': body})
        code = 200
        if pathname.endswith('/readiness'):
            data = {'status': 'ready', 'build_id': 'DEMO-POLICY'}
        elif pathname.endswith('/capabilities'):
            data = {'graph': {'available': False}, 'judge': {'available': False}, 'report_schema_version': 'DEMO'}
        elif pathname.endswith('/evaluation-options'):
            data = OPTIONS
        elif pathname.endswith('/estimate'):
            data = estimate(body)
        elif pathname == '/api/v1/evaluation-jobs' and req.method == 'POST':
            value = estimate(body)
            job = {'job_id': f'DEMO-JOB-{len(state["jobs"])+1}', 'status': 'completed', 'estimate': value,
                   'progress': {'completed_questions': value['total_questions'], 'total_questions': value['total_questions'],
                                'completed_runs': len(value['configurations']), 'total_runs': len(value['configurations']), 'current_query_id': None},
                   'runs': [{'run_id': f'DEMO-RUN-{i}' if len(value['configurations']) > 1 else 'DEMO-RUN', 'status': 'completed'} for i, _ in enumerate(value['configurations'])],
                   'budget': {'logical_calls': value['logical_calls_max'], 'http_attempts': value['logical_calls_max'],
                              'reserved_upper_cny': .1, 'actual_billed_cny': None, 'logical_limit': value['logical_calls_max'],
                              'http_limit': value['http_attempts_max'], 'cost_limit_cny': value['cost_ceiling_cny'], 'pending_request': None},
                   'error': None, 'created_at': '2026-11-10T02:00:00Z', 'updated_at': '2026-11-10T02:01:00Z'}
            state['jobs'].append(job)
            data = job
        elif pathname == '/api/v1/evaluation-jobs':
            data = {'items': state['jobs'], 'total': len(state['jobs'])}
        elif '/evaluation-jobs/' in pathname:
            data = next(j for j in state['jobs'] if pathname.endswith(j['job_id']))
        elif pathname == '/api/v1/evaluations':
            data = {'items': [{'run_id': 'DEMO-RUN', 'status': 'completed', 'created_at': '2026-11-10T02:00:00Z', 'summary': METRICS}], 'total': 1}
        elif '/evaluations/' in pathname:
            data = {**DETAIL, 'run_id': pathname.rsplit('/', 1)[1]}
        elif pathname.endswith('/answer'):
            if state['fail_answer']:
                code = 502
                data = {'request_id': 'DEMO-ERROR', 'error': {'code': 'DEMO_GENERATION_ERROR', 'message': '演示错误：生成服务暂不可用，请核查已保留证据。', 'details': {'retrieval': RETRIEVAL}}}
            else:
                data = copy.deepcopy(ANSWER)
                history = body.get('history') or []
                data['context'] = {'mode': 'bounded_history_rewrite' if history else 'independent',
                                   'history_messages_used': len(history), 'retrieval_query': QUESTION}
        elif pathname.endswith('/search'):
            data = RETRIEVAL
        elif '/documents/' in pathname:
            data = DOC
        elif pathname.endswith('/extensions/reports'):
            data = {'items': [], 'total': 0}
        else:
            code = 404
            data = {'request_id': 'DEMO', 'error': {'code': 'DEMO_NOT_FOUND', 'message': '隔离演示接口未配置'}}
        return route.fulfill(status=code, content_type='application/json', body=json.dumps(data, ensure_ascii=False))

    context.route('**/*', handle)
    context.add_init_script("""document.addEventListener('DOMContentLoaded',()=>{const banner=document.createElement('div');banner.textContent='演示数据 · 真实前端界面 / 隔离接口 · 非真实政策、费用或评测成绩';banner.setAttribute('data-demo-banner','true');banner.style.cssText='position:fixed;bottom:0;left:0;right:0;z-index:99999;background:#fff0c2;color:#704400;text-align:center;font:14px Microsoft YaHei,sans-serif;padding:6px;pointer-events:none';document.body.append(banner)})""")
    return state
