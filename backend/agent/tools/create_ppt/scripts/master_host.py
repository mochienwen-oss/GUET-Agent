"""Host original PPT Skill commands with task-local files and product state."""
import base64
import copy
from datetime import datetime
import json
import logging
from pathlib import Path
from uuid import uuid4

from agent.core.request_control import check_cancelled
from agent.runtime.loop import run_turn
from agent.runtime import registry
from core.errors import ApiError
from core.paths import BACKEND_ROOT
from .master_workspace import MasterWorkspace, SKILL_DIR, snapshot_bytes
from agent.skills.ppt_master import get as get_skill
from . import chat_history
from . import context_budget
from .reading import PptFileWorkspace

logger = logging.getLogger('uvicorn.error')

PPT_PROMPT = (BACKEND_ROOT / 'prompts/ppt-agent.txt').read_text(encoding='utf-8').strip()

TOOL_STEPS = {'search_web': '查找公开资料', 'read_webpage': '阅读参考资料',
              'search_regulations': '检索校园资料', 'update_presentation_brief': '整理制作需求',
              'set_revision_scope': '确定修改页面', 'read_file': '阅读制作资料',
              'write_file': '制作页面与大纲', 'edit_file': '修改页面内容',
              'execute_python': '处理页面与导出文件'}


def _save(state):
    from .subgraph import save_state
    save_state(state['task_id'], state)


def _exported(session):
    return next((item for item in reversed(session['commands'])
                 if item['script'] == 'svg_to_pptx.py' and item['exit_code'] == 0
                 and not item.get('stale') and item.get('output')), None)


def confirmation_action(stage):
    if stage == 'stage1':
        return {
            'action_label': '采用内容方向，继续',
            'action_message': '我确认采用当前内容方向，继续完善大纲与设计。',
            'action_hint': '点击按钮只确认当前内容方向；大纲与设计会另行确认。需要调整请直接发送意见。'}
    if stage == 'stage2':
        return {
            'action_label': '采用大纲与设计，开始制作',
            'action_message': '我确认采用当前大纲与设计，开始制作页面。',
            'action_hint': '点击按钮会记录你对当前大纲与设计的确认并开始制作；需要调整请直接发送意见。'}
    return {'action_label': None, 'action_message': None,
            'action_hint': '请直接发送补充信息或处理意见，当前没有需要批准的方案。'}


def _confirmation_required(session):
    stage = next((item for item in ('stage1', 'stage2') if item not in session['approved']), None)
    if stage is None:
        return None
    action = confirmation_action(stage)
    return ('内容方向尚未确认。' if stage == 'stage1' else
            '内容方向已确认，大纲与设计尚未确认。') + (
        '先整理当前待确认方案并结束本轮，等待用户点击当前工作区的“%s”。'
        '不需要另开确认网页，回复“已确认”不等于按钮确认。' % action['action_label'])


def _proposal_text(state, stage):
    session = state.get('master') or {}
    if session.get('proposal_stage') != stage:
        return None
    path = Path(session['project']) / 'outline.md'
    if not path.is_file():
        return None
    text = path.read_text(encoding='utf-8-sig').strip()
    return text or None


def validate_confirmation(state, confirmation_id, approved):
    pending = state.get('pending_user') or {}
    if state.get('stage') != 'await_design' or confirmation_id != pending.get('confirmation_id'):
        raise ApiError(409, '策划确认已变化，请刷新当前任务')
    expected = next((item for item in ('stage1', 'stage2')
                     if item not in state['master']['approved']), None)
    if pending.get('stage') not in {'stage1', 'stage2'} or pending['stage'] != expected:
        raise ApiError(409, '当前没有可采用的方案，请直接在聊天中补充意见')
    proposal = _proposal_text(state, pending['stage'])
    if approved and (proposal is None or proposal != pending.get('proposal')):
        raise ApiError(409, '待确认内容缺少本阶段实际提交的方案，或方案文件已经变化，未记录确认。请在聊天中让助手更新大纲与设计后再采用。')


def _catalog(state, project, *, previous=False):
    skill = get_skill('ppt_master')
    catalog = (
        '已安装Skill：%s\nSKILL_DIR=%s\nPROJECT_PATH=%s\n入口：%s\n'
        '文件工具支持@project/、@skill/%s。'
        'execute_python的script使用Skill脚本名，文件参数使用上述路径。'
        '页面只写 svg_output/，导出使用 svg_to_pptx.py @project/，不要另建发布目录。'
    ) % (skill.slug or skill.name, SKILL_DIR, Path(project), skill.entry,
         '；修订时可用@previous/只读查看上版' if previous else '')
    if state['brief'].get('material_policy') == 'provided_only':
        catalog += '\n此历史任务要求仅使用提供材料，除非用户明确改变要求，不补入外部事实。'
    from .web_search import availability
    return catalog + '\n公开搜索当前能力：' + availability()['reason']


def _svg_snapshot(project):
    """修订前的页面快照：base64 存 state（可序列化），不复制整个工程。"""
    folder = Path(project) / 'svg_output'
    return ({path.name: base64.b64encode(path.read_bytes()).decode('ascii')
             for path in folder.glob('*.svg')} if folder.is_dir() else {})


def _restore_snapshot(project, snapshot):
    folder = Path(project) / 'svg_output'
    if not folder.is_dir() or not snapshot:
        return
    for path in folder.glob('*.svg'):
        if path.name not in snapshot:
            path.unlink()
    for name, content in snapshot.items():
        (folder / name).write_bytes(snapshot_bytes(snapshot, name))


def start(state, *, revision=None, keep_history=False):
    from .subgraph import _workdir
    inplace = bool(revision) and bool(state.get('master'))
    if inplace:
        # 修订直接在现有工程上改，不再复制整份副本
        project = Path(state['master']['project']).resolve()
        number = state.get('revision', 0)
        snapshot = _svg_snapshot(project)
    else:
        number = state.get('revision', 0) + 1
        project = Path(_workdir()).resolve() / state['task_id'] / ('v%d' % number) / (
            'project_' + uuid4().hex[:8] + '_' + datetime.now().strftime('%Y%m%d'))
        snapshot = None
    previous_session = state.get('master') if keep_history else None
    previous_messages = previous_session['messages'] if previous_session else None
    session = {'engine': 'native-tools/1', 'revision': number, 'project': str(project),
               'messages': [], 'approved': [], 'api_requests': 0, 'usage': [], 'commands': []}
    # 产品形态为“一次生成”：不再等待内容方向/大纲的按钮确认，两阶段直接视为已采用。
    session['approved'] = ['stage1', 'stage2']
    workspace = MasterWorkspace(project)
    if revision:
        if revision['action'] == 'rebuild':
            for path in (project / 'svg_output').glob('*.svg'):
                path.unlink()
            snapshot = {}
        (project / 'validation').mkdir(exist_ok=True)
        (project / 'svg_output').mkdir(exist_ok=True)
        session.update(previous_project=None,
                       previous_pages=copy.deepcopy(state['slides']),
                       previous_snapshot=snapshot or {},
                       target_ids=revision['target_ids'], revision_action=revision['action'],
                       approved=['stage1', 'stage2'])
    workspace.initialize(state)
    catalog = _catalog(state, project, previous=bool(session.get('previous_project')))
    session['messages'] = [{'role': 'system', 'content': PPT_PROMPT},
                           {'role': 'system', 'content': catalog},
                           {'role': 'user', 'content': json.dumps({
                               'brief': state['brief'], 'revision_request': revision,
                               'confirmed_stages': session['approved']}, ensure_ascii=False)}]
    if previous_session:
        session['messages'] = previous_messages
        session['api_requests'] = previous_session['api_requests']
        session['usage'] = previous_session['usage']
        session['read_files'] = previous_session.get('read_files', {})
        previous_session.clear()
        previous_session.update(session)
        session = previous_session
        session['messages'][1]['content'] = catalog
    elif state.get('chat_workspace'):
        session['messages'].extend(chat_history.api_history(state))
    state.update(master=session, pending_user=None, errors=[], activity='正在读取制作需求与参考资料')
    session['messages'][1]['content'] = _catalog(state, project,
                                                 previous=bool(session.get('previous_project')))
    _save(state)


def _option_value(arguments, short, long):
    for index, value in enumerate(arguments):
        if value in (short, long) and index + 1 < len(arguments):
            return arguments[index + 1]
        for prefix in (short + '=', long + '='):
            if value.startswith(prefix):
                return value[len(prefix):]
    return None


def _project_relative(value, project):
    """把 @project/ 或绝对路径换算成工程内相对路径；不属于工程时返回 None。"""
    text = str(value).replace('\\', '/').strip().rstrip('/')
    if text in {'@project', ''}:
        return ''
    if text.startswith('@project/'):
        return text[len('@project/'):]
    if '/' not in text and ':' not in text:
        # 脚本以工程目录为 cwd，裸相对名按工程内路径解释
        return text
    try:
        return Path(text).resolve().relative_to(Path(project).resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _files(state):
    session = state['master']
    workspace = MasterWorkspace(session['project'])

    def written(path):
        if path.startswith(('svg_output/', 'notes/')) or path in {'design_spec.md', 'spec_lock.md', 'animations.json'}:
            for command in session['commands']:
                if command['script'] == 'svg_to_pptx.py':
                    command['stale'] = True

    def before_write(path):
        if state.get('revision_pending'):
            raise ValueError('修改已有成品前请先根据用户要求确定本轮修改范围')
        if path.split('/')[0] in {'sources', 'validation', 'exports', 'svg_final'}:
            raise ValueError('来源和检查/导出结果由宿主或原版脚本管理，不能直接写入')
        if path in {'result.json', 'template_selection.json', 'template_install.json'}:
            raise ValueError('不能伪造用户确认或模板安装回执')
        if (path.startswith('svg_output/') or path in {'design_spec.md', 'spec_lock.md'}) \
                and not {'stage1', 'stage2'}.issubset(session['approved']):
            raise ValueError(_confirmation_required(session))
        if session.get('revision_action') and path.startswith('svg_output/'):
            protected = {p['master_file'] for p in session['previous_pages']
                         if p['slide_id'] not in session['target_ids']}
            if path.removeprefix('svg_output/') in protected:
                raise ValueError('不能修改未选页面')

    def run_script(script, arguments):
        name = Path(script).name
        if name == 'server.py':
            raise ValueError('当前工作区已提供制作中页面预览和真实确认，不启动独立预览或确认服务器；请继续保存完整SVG页面')
        if '/' in script or '\\' in script:
            resolved = (SKILL_DIR / script.removeprefix('@skill/')).resolve()
            if resolved != (SKILL_DIR / 'scripts' / name).resolve():
                raise ValueError('仅执行已安装Skill的允许脚本')
        approved = session['approved']
        planning_read = arguments in (['--help'], ['-h']) or name in {'attribution_guard.py', 'text_measure.py', 'svg_position_calculator.py',
                                 'visualization_recall.py', 'analyze_images.py'}
        planning_read = planning_read or (name == 'project_manager.py' and arguments and arguments[0] in {'info', 'page-context', 'page-context-report'})
        if state.get('revision_pending') and not planning_read:
            raise ValueError('执行制作或导出前请先确定本轮修改范围')
        if not planning_read and ('stage1' not in approved or
                ('stage2' not in approved and name != 'apply_template.py')):
            raise ValueError(_confirmation_required(session))
        if name == 'apply_template.py' and 'stage2' in approved:
            raise ValueError('模板安装应在采用内容方向后、采用大纲与设计前完成')
        # 导出与检查只认当前工程的 svg_output；防止自建发布目录导致报告与实际页面不一致
        if name == 'svg_to_pptx.py':
            source = _option_value(arguments, '-s', '--source')
            if source is not None and _project_relative(source, session['project']) != 'svg_output':
                raise ValueError('导出源只能是当前工程的 svg_output：请使用 svg_to_pptx.py ["@project/"]，'
                                 '不要另建发布目录，也不要用 -s 指向其他位置')
        if name == 'svg_quality_checker.py' and arguments and not arguments[0].startswith('-'):
            if _project_relative(arguments[0], session['project']) not in {'', 'svg_output'}:
                raise ValueError('质量检查只针对当前工程根目录或 svg_output：请使用 '
                                 'svg_quality_checker.py ["@project/", "--canonical-authoring", "--stage", "final", "--json"]')
        before = {path: (path.stat().st_mtime_ns, path.stat().st_size)
                  for path in workspace.folder.rglob('*.pptx')} if name == 'svg_to_pptx.py' else {}
        result = workspace.run(name, arguments)
        receipt = {'script': name, 'arguments': arguments, 'exit_code': result['exit_code']}
        if name == 'svg_to_pptx.py' and result['exit_code'] == 0:
            outputs = [path for path in workspace.folder.rglob('*.pptx')
                       if before.get(path) != (path.stat().st_mtime_ns, path.stat().st_size)]
            if outputs:
                receipt['output'] = str(max(outputs, key=lambda path: path.stat().st_mtime_ns))
        session['commands'].append(receipt)
        output = {'status': 'success' if result['exit_code'] == 0 else 'error', 'data': result}
        if result['exit_code']:
            output['error'] = {'code': 'script_failed', 'detail': result.get('output', '')[-4000:] or '原版辅助命令未成功完成'}
        return output

    resources = {'@skill': SKILL_DIR}
    if session.get('previous_project'):
        resources['@previous'] = Path(session['previous_project'])
    return PptFileWorkspace(workspace.folder, resources, read_state=session.setdefault('read_files', {}),
                         before_write=before_write, run_script=run_script, on_written=written)


def _collect_sources(state, result):
    for source in result.get('sources') or []:
        eid = 'kb_' + uuid4().hex[:12]
        state.setdefault('evidence', {})[eid] = {
            'evidence_id': eid, 'title': source['title'], 'text': source['text'],
            'heading_path': source.get('heading_path') or [], 'scope': '', 'date': None,
            'source_revision': result.get('source_revision')}
        source['evidence_id'] = eid
    if result.get('sources'):
        MasterWorkspace(state['master']['project']).import_materials(state)


def _register_material(state, material, metadata):
    """The active host owns this mutation; do not enter the upload workflow."""
    for known in state.get('materials') or []:
        if material.url and known.get('url') == material.url and known.get('text') == material.text:
            return {'material_id': known['material_id'], 'evidence_id': known['material_id']}
    if len(state.get('materials') or []) >= 20:
        raise ValueError('当前任务已有20份资料，请先选定必要来源')
    state.setdefault('materials', []).append(material.model_dump())
    evidence = material.evidence()
    evidence.update({key: metadata[key] for key in (
        'final_url', 'accessed_at', 'published_at', 'content_scope', 'reading_status',
        'full_paper_verified', 'untrusted_content') if key in metadata})
    state.setdefault('evidence', {})[material.material_id] = evidence
    MasterWorkspace(state['master']['project']).import_materials(state)
    _save(state)
    return {'material_id': material.material_id, 'evidence_id': material.material_id}


def _set_revision_scope(state, action, slide_ids, reason):
    if not state.get('revision_pending'):
        raise ValueError('当前没有等待确定范围的成品修改请求')
    known = {slide['slide_id'] for slide in state.get('slides') or []}
    ids = list(slide_ids)
    if action == 'rebuild':
        ids = [slide['slide_id'] for slide in state['slides']]
    if action != 'append' and not ids:
        raise ValueError('请根据用户要求指定实际页面，或明确选择整份重做')
    if len(set(ids)) != len(ids) or any(sid not in known for sid in ids):
        raise ValueError('修改范围含有不属于当前作品的页面')
    if action == 'split' and len(ids) != 1:
        raise ValueError('一次拆分一页；其他页面修改可另用replace')
    if not set(state.get('stale_slide_ids') or []).issubset(ids):
        raise ValueError('已有引用材料改变，请将受影响页面包含在本轮修改中')
    revision = {'action': action, 'target_ids': ids, 'feedback': reason}
    state['revision_pending'] = False
    start(state, revision=revision, keep_history=True)
    state['last_revision'] = {**revision, 'user_message': state.get('last_user_message', '')}
    _save(state)
    return {'status': 'success', 'data': {'action': action, 'slide_ids': ids,
                                        'project': state['master']['project'],
                                        'confirmed_stages': state['master']['approved'],
                                        'instruction': '先读取新副本中的实际文件；整份重做需要重新确认方案。'}}


def _changed_pages(state):
    from pptx import Presentation
    from .master_workspace import page_content
    session = state['master']
    previous = {page['slide_id']: page for page in session.get('previous_pages') or []}
    previous_root = Path(session['previous_project']) if session.get('previous_project') else None
    snapshot = session.get('previous_snapshot') or {}
    old_deck = (Presentation(Path(previous_root).parent / 'presentation.pptx')
                if previous and previous_root is not None else None)
    new_deck = Presentation(Path(session['project']).parent / 'presentation.pptx')
    old_positions = {page['slide_id']: index for index, page in enumerate(session.get('previous_pages') or [])}
    updated = []
    for number, slide in enumerate(state.get('slides') or [], 1):
        old = previous.get(slide['slide_id'])
        changed = not old
        if old:
            before = ((previous_root / 'svg_output' / old['master_file']).read_bytes()
                      if previous_root is not None
                      else snapshot_bytes(snapshot, old['master_file']))
            after = (Path(session['project']) / 'svg_output' / slide['master_file']).read_bytes()
            changed = (before != after or
                       (old_deck is not None and
                        page_content(old_deck.slides[old_positions[slide['slide_id']]]) !=
                        page_content(new_deck.slides[number - 1])))
        if changed:
            updated.append({'slide_id': slide['slide_id'], 'page_number': number, 'title': slide['title']})
    current_ids = {page['slide_id'] for page in state.get('slides') or []}
    updated.extend({'slide_id': sid, 'title': page['title'], 'removed': True}
                   for sid, page in previous.items() if sid not in current_ids)
    return updated


def advance(state, *, confirmation=None):
    chat_history.ensure_history(state)
    if confirmation is not None:
        validate_confirmation(state, confirmation['confirmation_id'], confirmation['approved'])
        pending = state.get('pending_user') or {}
        stage = pending.get('stage')
        action = confirmation_action(stage)
        feedback = confirmation.get('feedback', '').strip()
        message = action['action_message'] if confirmation['approved'] else '请先调整或补充当前方案。'
        if feedback:
            message = feedback if feedback == message or feedback.startswith(message + '\n') else message + '\n' + feedback
        state.setdefault('confirmation_records', []).append({
            'confirmation_id': pending['confirmation_id'], 'stage': stage,
            'approved': bool(confirmation['approved']), 'owner_id': state['owner_id'],
            'feedback': feedback, 'proposal': pending.get('proposal', ''),
            'action_label': action['action_label'], 'action_message': action['action_message'],
            'created_at': datetime.now().astimezone().isoformat()})
        if confirmation['approved'] and stage in {'stage1', 'stage2'}:
            state['master']['approved'].append(stage)
        state['master'].pop('proposal_stage', None)
        state['master']['messages'].append({'role': 'user', 'content': message})
        confirmed = state['master']['approved']
        state['master']['messages'].append({'role': 'system', 'content':
            '宿主记录的真实用户确认阶段：' + ', '.join(confirmed) + '\n' + (
                '本次按钮只确认了内容方向。接下来整理并保存大纲与设计建议，再等待另一次工作区按钮确认；当前不能制作页面或写入最终设计规格。'
                if confirmation['approved'] and stage == 'stage1' else
                '大纲与设计已通过当前工作区按钮确认，可以按已确认方案制作，不再要求用户回复确认或打开其他网页。'
                if confirmation['approved'] and stage == 'stage2' else
                '用户本次没有采用待确认方案；请按实际意见调整并重新提交，不能将普通文字作为确认。')})
        state['pending_user'] = None
    elif state.get('stage') == 'await_design':
        raise ApiError(409, '请先提交策划确认或补充意见')
    try:
        if not state.get('master') or state.get('stage') in {'brief_changed', 'materials_changed'}:
            start(state)
        session = state['master']
        if session.get('engine') != 'native-tools/1':
            raise ApiError(409, '该任务使用旧执行协议，请重新生成以启用外挂Skill')
        session['messages'][0]['content'] = PPT_PROMPT
        session['messages'][1]['content'] = _catalog(state, session['project'],
                                                     previous=bool(session.get('previous_project')))
        state.update(stage='generating', next_node='master')
        files = _files(state)
        from .web_search import web_tools
        from .workspace_actions import tools as action_tools
        product_tools = action_tools(state, lambda **args: _set_revision_scope(state, **args))
        public_tools = web_tools(state, lambda material, metadata: _register_material(state, material, metadata))
        available_tools = {tool.name: tool for tool in [*product_tools, *public_tools]}
        tools = [tool.schema() for tool in [*files.tools(), *available_tools.values()]] + registry.schemas()
        next(tool for tool in tools if tool['function']['name'] == 'execute_python')['function']['description'] += (
            ' PPT脚本按原版参数逐条执行，stdout/stderr和退出码原样返回；'
            '文件可使用当前项目绝对路径或@project/。本接口没有stdin通道，批量输入使用原版JSON文件方式。'
            '当前工作区承接聊天确认与页面预览，不另起独立服务器。')
        last_output = {}

        def execute(name, arguments):
            nonlocal files, last_output
            if name == 'search_regulations':
                result = registry.call(name, arguments)
                _collect_sources(state, result)
            elif name in available_tools:
                result = registry.execute(available_tools[name], arguments)
                if name == 'set_revision_scope' and result['status'] == 'success':
                    files = _files(state)
            else:
                result = files.execute(name, arguments)
            writes = files.take_writes()
            if any(changed for _, changed in writes):
                result.setdefault('data', {})['artifact_changed'] = True
            for path, changed in writes:
                if path == 'outline.md':
                    stage = next((item for item in ('stage1', 'stage2') if item not in session['approved']), None)
                    target = Path(session['project']) / path
                    if stage and target.read_text(encoding='utf-8-sig').strip():
                        session['proposal_stage'] = stage
                    else:
                        session.pop('proposal_stage', None)
            last_output = result
            return result

        def progress(kind, data):
            if kind == 'request':
                session['api_requests'] += 1
                state['_active_step'] = ('理解修改要求' if state.get('revision_pending') else
                    '制作页面' if 'stage2' in session['approved'] else '整理大纲与汇报思路')
                state['activity'] = '正在' + state['_active_step']
            elif kind == 'tool_start':
                state['_active_step'] = TOOL_STEPS.get(data['name'], '制作')
                state['activity'] = '正在' + state['_active_step']
            elif kind == 'tool':
                state.setdefault('tool_calls', []).append(data)
                output = last_output.get('data') or {}
                if data['status'] in {'error', 'unavailable'}:
                    state['activity'] = '本步未完成，正在核对具体原因'
                elif output.get('output_kind') in {'svg_fragment', 'svg_fragments'}:
                    state['activity'] = '已取得图形片段，尚未保存完整页面'
                elif output.get('artifact_changed'):
                    pages = len(list((Path(session['project']) / 'svg_output').glob('*.svg')))
                    state['activity'] = '文件已保存；当前有%d个页面文件' % pages
                elif data['name'] == 'read_file':
                    state['activity'] = '已读取制作资料'
                elif data['name'] == 'execute_python':
                    state['activity'] = '本地辅助命令已返回，尚未代表整份制作完成'
                else:
                    state['activity'] = TOOL_STEPS.get(data['name'], '本步处理') + '已完成'
            elif kind == 'interrupted':
                state['activity'] = '本轮制作已中断，进度已保留'
            _save(state)

        def terminal(name, output):
            return '页面已实际导出，接下来生成正式预览。' if not state.get('revision_pending') and _exported(session) else None

        def record_usage(usage):
            session['usage'].append(usage)
            _save(state)

        if not state.get('revision_pending') and _exported(session):
            result = {'text': '', 'paused': False, 'calls': [], 'results': [], 'usage': []}
        else:
            def prepare(step, messages):
                # run_turn 内部最多连续跑 40 步，只在进入循环前折一次压不住后面的增长，
                # 所以每次请求前都检查；只改正文不动结构，tool_call 与 tool_result 仍然成对。
                folded = context_budget.fold_tool_results(messages)
                if folded['folded']:
                    logger.info('ppt_context_folded task=%s step=%d folded=%d chars=%d->%d',
                                state['task_id'], step + 1, folded['folded'],
                                folded['before'], folded['after'])

            result = run_turn(session['messages'], task='ppt', tools=tools, execute=execute,
                              max_steps=40, max_calls=None, on_progress=progress,
                              on_tool_result=terminal, on_usage=record_usage, prepare=prepare)
        check_cancelled()
        if _exported(session) and not state.get('revision_pending') and {'stage1', 'stage2'}.issubset(session['approved']):
            state['activity'] = '正在导出并生成实际页面预览'
            state['_active_step'] = '导出与预览'
            _save(state)
            state.update(MasterWorkspace(session['project']).publish(state, session), stage='draft', next_node=None,
                         activity='草稿已生成，可预览并修改页面')
            updated = _changed_pages(state)
            selected = state.get('selected_slide_id')
            if selected and selected not in {page['slide_id'] for page in state['slides']}:
                state['selected_slide_id'] = None
            changed_numbers = [str(page['page_number']) for page in updated if not page.get('removed')]
            text = ('已更新第%s页，文件与预览已同步。' % '、'.join(changed_numbers)
                    if session.get('previous_pages') and changed_numbers else
                    '已重新导出当前版本并刷新预览。' if session.get('previous_pages') else
                    '已生成%d页草稿，可以查看页面并继续修改。' % len(state['slides']))
            if session.get('previous_pages') and not updated:
                text += '未检测到页面内容变化，尚未完成你要求的调整。'
            removed = [page['title'] for page in updated if page.get('removed')]
            if removed:
                text += '已移除页面：' + '、'.join(removed) + '。'
            chat_history.record(state, 'assistant', text, updated_pages=updated,
                                outline_available=bool(chat_history.outline(state)))
            state.pop('revision_pending', None)
            state.pop('return_stage', None)
            # 本轮已交付：清掉修改范围，避免下一轮被误判成"没有实际修改"；
            # 同时把"上一版"基准刷新为刚发布的页面，供下一次比较。
            session.pop('revision_action', None)
            session.pop('target_ids', None)
            session['previous_pages'] = copy.deepcopy(state['slides'])
            session['previous_snapshot'] = _svg_snapshot(session['project'])
        elif result['paused']:
            state.update(stage='master_continue', next_node='master')
        else:
            if state.get('revision_pending'):
                state.update(stage=state.pop('return_stage', 'draft'), next_node=None)
                state.pop('revision_pending', None)
                chat_history.record(state, 'assistant', result['text'],
                                    outline_available=bool(chat_history.outline(state)))
                _save(state)
                return state
            # 一次生成形态：本轮结束即结束，不再产出待确认方案或确认按钮。
            state.update(stage='dialogue', next_node=None, pending_user=None,
                         activity='本轮已结束，可直接提出修改要求')
            chat_history.record(state, 'assistant', result['text'],
                                outline_available=bool(chat_history.outline(state)))
        _save(state)
        return state
    except BaseException as error:
        from . import background
        if background.pausing(state['task_id']):
            state.update(stage='paused', next_node='master', activity='正在暂停，保存已完成的内容')
            _save(state)
            raise
        state.update(stage='paused', next_node='master', activity='制作中断，可核对已保存进度后继续')
        detail = str(error) if isinstance(error, (ValueError, TimeoutError, InterruptedError)) else '本次执行未完成，请检查本地服务日志后继续'
        message = '本轮%s未能完成。此前保存的资料和页面仍保留，可以稍后继续或补充要求；未完成的步骤不会作为成品交付。' % state.get('_active_step', '制作')
        state.setdefault('errors', []).append({'node': 'runtime', 'detail': detail, 'user_message': message})
        chat_history.record(state, 'assistant', message)
        _save(state)
        raise
