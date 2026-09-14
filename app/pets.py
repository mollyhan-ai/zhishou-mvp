"""Local pet index and reviewed animal-record endpoints."""
import time
from flask import Blueprint, jsonify, request
from . import db, patient_records as records
from .management import local_access, failure

bp = Blueprint('pets', __name__)
bp.before_request(local_access)


def get_pet(pet_id, exclude_session=None):
    if not pet_id:
        return None
    conn = db.connect()
    try:
        row = conn.execute('SELECT * FROM pets WHERE id=?', (pet_id,)).fetchone()
        return records.public_pet(conn, row, exclude_session) if row else None
    finally:
        conn.close()


@bp.post('/api/pets/search')
def search():
    # Query text stays out of URL/access logs and browser history.
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return failure('搜索格式不正确。')
    query, offset = body.get('query', ''), body.get('offset', 0)
    if not isinstance(query, str) or len(query) > 80 or type(offset) is not int or offset < 0:
        return failure('请输入 80 字以内的名称，页码须有效。')
    conn = db.connect()
    try:
        needle = query.strip()
        where = "p.merged_into IS NULL AND (instr(lower(p.name),lower(?))>0 OR instr(lower(p.record_number),lower(?))>0 OR EXISTS (SELECT 1 FROM json_each(p.profile_json,'$.aliases') a WHERE instr(lower(a.value),lower(?))>0))"
        args = (needle,needle,needle)
        total = conn.execute('SELECT count(*) FROM pets p WHERE '+where,args).fetchone()[0]
        rows = conn.execute('SELECT p.*,count(s.id) AS session_count FROM pets p LEFT JOIN sessions s ON s.pet_id=p.id WHERE '+where+
            ' GROUP BY p.id ORDER BY coalesce(max(s.created_at),p.created_at) DESC,p.id DESC LIMIT 21 OFFSET ?',(*args,offset)).fetchall()
        return jsonify(ok=True, pets=[records.public_pet(conn,r) for r in rows[:20]],total=total,has_more=len(rows)>20)
    finally:
        conn.close()


@bp.post('/api/pets')
def create():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return failure('请填写宠物名称和种类。')
    name, species = body.get('name'), body.get('species')
    if not isinstance(name, str) or not name.strip() or len(name.strip())>80 or species not in ('dog','cat','other','unknown'):
        return failure('名称须为 1–80 字，并选择有效种类。')
    pet_id = db.new_id('pet')
    conn = db.connect()
    try:
        # Same names are allowed: identity is the stable id, never a name match.
        conn.execute('BEGIN IMMEDIATE')
        p=records.create_record(conn,records.normalized({'name':name.strip(),'species':species}),'人工建档（旧版入口）')
        pet_id=p['id']
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True, pet=get_pet(pet_id)), 201


@bp.put('/api/sessions/<sid>/pet')
def associate(sid):
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {'pet_id'} or (body['pet_id'] is not None and not isinstance(body['pet_id'],str)):
        return failure('请选择有效档案。')
    pet_id = body['pet_id']
    conn = db.connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        sess = conn.execute('SELECT status,patient_context_json FROM sessions WHERE id=?',(sid,)).fetchone()
        if not sess:
            return failure('会话不存在。',404)
        if sess['status'] in ('transcribing','generating'):
            return failure('会话正在处理中，请完成后再关联档案。',409)
        if pet_id and not conn.execute('SELECT 1 FROM pets WHERE id=?',(pet_id,)).fetchone():
            return failure('宠物档案不存在。',404)
        if pet_id == '':
            return failure('请选择有效档案。')
        if sess['patient_context_json'] != '{}':
            return failure('此会话已有档案病史快照，请通过本次就诊对象卡片重新核对关联。',409)
        if pet_id:
            p=records.read_pet(conn,pet_id)
            profile=records.fields_of(p)
            if any(records.known(k,profile.get(k)) for k in records.PROFILE if k not in ('name','species')):
                return failure('此档案包含就诊背景，请通过本次就诊对象卡片核对后带入。',409)
        # Filing metadata only: no clinical content, revisions or confirmation change.
        conn.execute('UPDATE sessions SET pet_id=? WHERE id=?',(pet_id,sid))
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True, pet=get_pet(pet_id))


records.register(bp)
