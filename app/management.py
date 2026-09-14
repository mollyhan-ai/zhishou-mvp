"""Local management endpoints. Never return credentials or raw provider errors."""
import hmac
import json
import re
import ipaddress
import mimetypes
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests
from flask import Blueprint, current_app, jsonify, request, send_file
from . import db
from .config import get_config, apply_provider_settings
from .settings_store import BASE_URL, MODEL, save_config

bp = Blueprint('management', __name__)


def failure(message, status=400, **extra):
    return jsonify(ok=False, message=message, **extra), status


@bp.before_request
def local_access():
    try:
        local = ipaddress.ip_address(request.remote_addr).is_loopback
    except ValueError:
        local = False
    host = urlsplit(request.host_url).hostname
    if not local or host not in ('localhost', '127.0.0.1', '::1'):
        return failure('设置与历史会话仅限在本机打开。', 403)
    if request.headers.get('Sec-Fetch-Site') == 'cross-site':
        return failure('请从知兽页面操作。', 403)
    origin = request.headers.get('Origin')
    if origin and origin != request.host_url.rstrip('/'):
        return failure('请从知兽页面操作。', 403)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        token = request.headers.get('X-Management-Token', '')
        if not hmac.compare_digest(token.encode('utf-8'), current_app.config['MANAGEMENT_TOKEN'].encode('utf-8')):
            return failure('页面已过期，请刷新后再试。', 403)
        if not request.is_json:
            return failure('请求格式不正确。', 415)


@bp.get('/api/settings')
def settings():
    cfg = get_config()
    return jsonify(ok=True, asr_model=cfg.asr_model, llm_model=cfg.llm_model,
                   key_configured=bool(cfg.asr_api_key and cfg.llm_api_key),
                   asr_provider=cfg.asr_provider, recommended_model=MODEL)


@bp.put('/api/settings/doubao')
def update_settings():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {'api_key'}:
        return failure('请填写 API Key。')
    key = body['api_key']
    if not isinstance(key, str) or len(key) > 1024:
        return failure('API Key 格式不正确。')
    if not key.strip():
        return failure('请输入新密钥；不需要更新时无需保存。')
    with current_app.config['MANAGEMENT_LOCK']:
        try:
            values = save_config(Path(current_app.config['SETTINGS_ENV_PATH']), key)
        except ValueError as exc:
            return failure(str(exc))
        except OSError:
            return failure('配置未保存，请检查本机配置文件的写入权限。原配置仍有效。', 500, saved=False)
        apply_provider_settings(values)
    return jsonify(ok=True, saved=True, message='配置已保存并生效，无需重启。')


@bp.post('/api/settings/test')
def test_connection():
    cfg = get_config()
    missing = cfg.llm_missing()
    if missing:
        return failure('请先保存豆包 API Key，再测试连接。', 503,
                       error_kind='config', retryable=False, missing=missing)
    # Do not send the stored key to arbitrary user-supplied hosts or redirects.
    if cfg.llm_base_url.rstrip('/') != BASE_URL:
        return failure('当前测试仅支持火山方舟，请先保存豆包配置。')
    with current_app.config['MANAGEMENT_LOCK']:
        now = time.monotonic()
        if now < current_app.config['CONNECTION_TEST_AFTER']:
            return failure('请等待 30 秒后再测试。', 429)
        current_app.config['CONNECTION_TEST_AFTER'] = now + 30
    try:
        r = requests.post(BASE_URL + '/chat/completions',
            headers={'Authorization': 'Bearer ' + cfg.llm_api_key},
            json={'model': cfg.llm_model,
                  'messages': [{'role': 'user', 'content': '请只回复OK'}],
                  'thinking': {'type': 'disabled'}, 'max_tokens': 16},
            timeout=(5, 25), allow_redirects=False)
        try:
            data = r.json()
        except ValueError:
            data = {}
        data = data if isinstance(data, dict) else {}
        error = data.get('error')
        code = str(error.get('code', '')) if isinstance(error, dict) else ''
        if r.status_code != 200:
            if code == 'ModelNotOpen':
                msg = '模型尚未开通。请在火山方舟开通当前模型服务后重试。'
            elif r.status_code == 401:
                msg = '密钥无效或已失效，请重新创建并保存 API Key。'
            elif r.status_code == 403:
                msg = '此密钥没有模型访问权限，请检查火山方舟的授权。'
            elif r.status_code == 404:
                msg = '未找到模型或接入点，请在火山方舟核对当前模型是否可用。'
            elif r.status_code in (402, 429):
                msg = '额度不足或请求受限，请检查火山方舟的额度与调用限制。'
            else:
                msg = '豆包服务暂时不可用，请稍后重试。'
            return failure(msg, 502)
        choices = data.get('choices')
        choice = choices[0] if isinstance(choices, list) and choices else {}
        message = choice.get('message', {}) if isinstance(choice, dict) else {}
        content = message.get('content') if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip() or choice.get('finish_reason') != 'stop':
            return failure('接口已响应，但没有返回完整文字，连接测试未通过。', 502)
    except requests.RequestException:
        return failure('连接超时或网络不可用，请检查网络后再试。', 502)
    return jsonify(ok=True, message='文字接口连接成功。语音转写请用实际录音验证。')


@bp.get('/api/history')
def history():
    try:
        offset = int(request.args.get('offset', 0))
        if offset < 0:
            raise ValueError()
    except ValueError:
        return failure('页码不正确。')
    scope = request.args.get('scope', 'all')
    if scope not in ('all', 'notes'):
        return failure('会话范围不正确。')
    conditions, params = [], []
    if scope == 'notes':
        conditions.append("s.note_json IS NOT NULL")
    pet_id = request.args.get('pet_id')
    if pet_id:
        if pet_id == 'unassigned':
            conditions.append("s.pet_id IS NULL")
        else:
            conditions.append("s.pet_id = ?")
            params.append(pet_id)
    where = 'WHERE ' + ' AND '.join(conditions) if conditions else ''
    order = "COALESCE(s.note_generated_at, s.note_edited_at, s.created_at) DESC, s.id DESC" if scope == 'notes' else "s.created_at DESC, s.id DESC"
    conn = db.connect()
    try:
        rows = conn.execute(
            'SELECT s.id, s.created_at, s.status, s.confirmed_at, s.audio_name, s.note_generated_at, '
            's.note_edited_at, s.note_json, s.custom_summary, s.pet_id, p.name AS pet_name, p.record_number AS pet_number FROM sessions s '
            'LEFT JOIN pets p ON p.id=s.pet_id ' + where +
            ' ORDER BY ' + order + ' LIMIT 21 OFFSET ?', (*params, offset)).fetchall()
        total = conn.execute('SELECT COUNT(*) FROM sessions s ' + where, params).fetchone()[0]
    finally:
        conn.close()
    sessions = []
    for row in rows[:20]:
        item = dict(row)
        raw_note = item.pop('note_json')
        item['has_note'] = raw_note is not None
        # Only a bounded Subjective excerpt leaves the list endpoint. Clinical
        # content remains in SQLite; it is not written into analytics or browser storage.
        try:
            note = json.loads(raw_note or '{}')
            subjective = (note.get('subjective_sections') or {}).get('chief_complaint',note.get('subjective', '')) if isinstance(note, dict) else ''
        except (ValueError, TypeError):
            subjective = ''
        subjective = item.pop('custom_summary') or subjective
        item['preview'] = re.sub(r'\s+', ' ', subjective).strip()[:80] if isinstance(subjective, str) else ''
        item['display_name'] = item.pop('pet_name') or item.get('pet_number') or '未命名会话'
        sessions.append(item)
    return jsonify(ok=True, sessions=sessions, has_more=len(rows) > 20, total=total)


@bp.get('/api/sessions/<sid>/audio')
def audio(sid):
    sess = db.get_session(sid)
    if not sess or not sess.get('audio_path'):
        return failure('这次会话没有录音，或录音已删除。', 404)
    path = Path(sess['audio_path']).resolve()
    root = Path(get_config().audio_dir).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return failure('录音文件不存在。', 404)
    response = send_file(path, mimetype=mimetypes.guess_type(str(path))[0] or 'application/octet-stream',
                         conditional=True)
    response.headers['Cache-Control'] = 'no-store'
    return response
