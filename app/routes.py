import json
import os
import time

from flask import Blueprint, current_app, jsonify, render_template, request, Response

from . import db, grounding, privacy, soap, history_context, evidence, audio_metadata
from .config import get_config
from .kb import answer as kb_answer
from .kb import store as kb_store
from .providers import ConfigError, ProviderError
from .providers import asr as asr_provider

bp = Blueprint("main", __name__)

ALLOWED_AUDIO = {".wav", ".mp3", ".m4a", ".mp4", ".webm", ".ogg", ".flac", ".aac", ".amr"}

PRIVACY_NOTICE = (
    "请不要在提问或病历中输入宠主姓名、手机号、身份证号、住址或病例号。"
    "系统默认只记录去标识化的使用指标（耗时、长度区间、是否被拒答），"
    "不记录音频、逐字稿或病历原文。"
)


def _err(exc, status):
    body = {
        "ok": False,
        "error_kind": getattr(exc, "kind", "provider"),
        "message": getattr(exc, "message", str(exc)),
        "detail": getattr(exc, "detail", None),
        "missing": getattr(exc, "missing", None),
        "retryable": getattr(exc, "kind", "provider") == "provider",
    }
    return jsonify(body), status


def _log(event, session_id=None, **payload):
    try:
        ref = privacy.session_ref(session_id) if session_id else None
        db.insert_event(event, ref, privacy.safe_payload(payload))
    except Exception:  # logging must never break the request
        current_app.logger.exception("event log failed")


def _session_view(sess: dict) -> dict:
    note = json.loads(sess["note_json"]) if sess.get("note_json") else None
    flags = json.loads(sess["flags_json"]) if sess.get("flags_json") else []
    from .pets import get_pet
    from .patient_records import candidate_view, review_needed
    conn=db.connect()
    try:
        obs=conn.execute('SELECT values_json FROM visit_observations WHERE session_id=?',(sess['id'],)).fetchone()
    finally: conn.close()
    return {
        "patient_observations": json.loads(obs[0]) if obs else {},
        **audio_metadata.view(sess),
        "pet": get_pet(sess.get("pet_id"),sess["id"]),
        "patient_context": json.loads(sess.get("patient_context_json") or "{}"),
        "patient_review_needed": review_needed(sess),
        "patient_review_by": sess.get("patient_review_by"),
        "patient_review_at": sess.get("patient_review_at"),
        # This is an unconfirmed session draft, not a patient record. It lets a
        # reviewer refresh the page without paying for another extraction.
        "patient_candidate": candidate_view(sess),
        "effective_history": history_context.active_text(sess),
        "id": sess["id"],
        "status": sess["status"],
        "custom_summary": sess.get("custom_summary"),
        "summary_revision": sess.get("summary_revision", 0),
        "error_message": sess.get("error_message"),
        "error_kind": sess.get("error_kind"),
        "audio_name": sess.get("audio_name"),
        "audio_bytes": sess.get("audio_bytes"),
        "has_audio": bool(sess.get("audio_path")),
        "transcript": sess.get("transcript"),
        "history_raw": sess.get("history_raw") or "",
        "history_summary": json.loads(sess.get("history_summary_json") or "{}"),
        "history_summary_stale": bool(sess.get("history_summary_stale")),
        "evidence_revision": sess.get("evidence_revision", 0),
        "note_revision": sess.get("note_revision", 0),
        "note_needs_review": bool(sess.get("note_needs_review")),
        "note": note,
        "flags": flags,
        "flag_summary": grounding.summarize(flags),
        "confirmed": bool(sess.get("confirmed_at")),
        "confirmed_at": sess.get("confirmed_at"),
        "confirmed_by": sess.get("confirmed_by"),
        "created_at": sess["created_at"],
        "updated_at": sess["updated_at"],
        "last_content_edited_at": sess.get("last_content_edited_at"),
        "note_generated_at": sess.get("note_generated_at"),
        "asr_used_provider": sess.get("asr_used_provider"),
        "asr_used_model": sess.get("asr_used_model"),
        "llm_used_model": sess.get("llm_used_model"),
    }


# --- pages -----------------------------------------------------------------

@bp.get("/")
def index():
    return render_template("index.html")


@bp.get("/api/status")
def status():
    cfg = get_config()
    s = cfg.status()
    s["privacy_notice"] = PRIVACY_NOTICE
    s["asr_language"] = cfg.asr_language
    s["ok"] = True
    return jsonify(s)


# --- sessions --------------------------------------------------------------

@bp.post("/api/sessions")
def create_session():
    sid = db.create_session()
    _log("session_created", sid, status="created")
    return jsonify({"ok": True, "session": _session_view(db.get_session(sid))})


@bp.get("/api/sessions")
def list_sessions():
    return jsonify({"ok": True, "sessions": db.list_sessions()})


@bp.get("/api/sessions/<sid>")
def get_session(sid):
    sess = db.get_session(sid)
    if not sess:
        return jsonify({"ok": False, "message": "会话不存在。"}), 404
    return jsonify({"ok": True, "session": _session_view(sess)})


@bp.put("/api/sessions/<sid>/summary")
def edit_session_summary(sid):
    body = request.get_json(silent=True)
    if (not isinstance(body, dict) or not isinstance(body.get("summary"), str)
            or len(body["summary"]) > 200 or type(body.get("revision")) is not int
            or body["revision"] < 0):
        return jsonify(ok=False, message="摘要最多 200 字，请重新打开编辑后保存。"), 400

    class SummaryConflict(Exception):
        pass

    def mutation(sess):
        if body["revision"] != sess.get("summary_revision", 0):
            raise SummaryConflict()
        # List metadata is never used as clinical evidence or exported as SOAP.
        return {"custom_summary": body["summary"].strip() or None,
                "summary_revision": sess.get("summary_revision", 0) + 1}

    try:
        sess = db.mutate_session(sid, mutation)
    except SummaryConflict:
        return jsonify(ok=False, message="摘要已在其他页面修改。请保留输入内容，刷新后重新编辑。"), 409
    except Exception:
        return jsonify(ok=False, message="摘要保存失败，输入内容已保留，请重试。"), 500
    if sess is None:
        return jsonify(ok=False, message="会话不存在。"), 404
    return jsonify(ok=True, saved=True, custom_summary=sess["custom_summary"],
                   summary_revision=sess["summary_revision"])


@bp.post("/api/sessions/<sid>/audio")
def upload_audio(sid):
    cfg = get_config()
    sess = db.get_session(sid)
    if not sess:
        return jsonify({"ok": False, "message": "会话不存在。"}), 404
    if "file" not in request.files:
        return jsonify({"ok": False, "error_kind": "input", "message": "没有收到音频文件。"}), 400

    try:
        metadata = audio_metadata.upload_fields(request.form, time.time())
    except ValueError as exc:
        return jsonify(ok=False,error_kind="input",message=str(exc)),400
    f = request.files["file"]
    name = f.filename or "recording.webm"
    ext = os.path.splitext(name)[1].lower() or ".webm"
    if ext not in ALLOWED_AUDIO:
        return jsonify({
            "ok": False, "error_kind": "input",
            "message": f"不支持的音频格式 {ext}。支持：{', '.join(sorted(ALLOWED_AUDIO))}",
        }), 400

    path = os.path.join(cfg.audio_dir, f"{sid}{ext}")
    f.save(path)
    size = os.path.getsize(path)
    if size > cfg.max_audio_mb * 1024 * 1024:
        os.remove(path)
        return jsonify({
            "ok": False, "error_kind": "input",
            "message": f"音频超过 {cfg.max_audio_mb}MB 上限。",
        }), 400

    db.update_session(sid, **metadata, audio_duration_seconds=audio_metadata.duration(path),
                      audio_path=path, audio_name=name, audio_bytes=size,
                      status="created", error_message=None, error_kind=None)
    _log("audio_uploaded", sid, audio_bytes_bucket=privacy.bucket(
        size, (0, 1_000_000, 5_000_000, 20_000_000, 50_000_000)))
    return jsonify({"ok": True, "session": _session_view(db.get_session(sid))})


@bp.post("/api/sessions/<sid>/transcribe")
def transcribe(sid):
    sess = db.get_session(sid)
    if not sess:
        return jsonify({"ok": False, "message": "会话不存在。"}), 404
    if not sess.get("audio_path"):
        return jsonify({"ok": False, "error_kind": "input", "message": "请先录音或上传音频。"}), 400

    db.update_session(sid, status="transcribing", error_message=None, error_kind=None)
    t0 = time.time()
    used_config = get_config()
    try:
        result = asr_provider.transcribe(sess["audio_path"])
    except ConfigError as exc:
        db.update_session(sid, status="failed", error_kind="config", error_message=exc.message)
        _log("transcribe_failed", sid, error_kind="config", ok=False)
        return _err(exc, 503)
    except ProviderError as exc:
        db.update_session(sid, status="failed", error_kind="provider", error_message=exc.message)
        _log("transcribe_failed", sid, error_kind="provider", ok=False)
        return _err(exc, 502)

    try:
        updated = evidence.change(sid, {"transcript": result["text"]}, sess['evidence_revision'])
    except evidence.ChangedDuringRequest:
        return _evidence_conflict()
    if not updated:
        return _evidence_conflict()
    db.mutate_session(sid, lambda current: {
        "asr_used_provider": used_config.asr_provider,
        "asr_used_language": used_config.asr_language,
        "asr_used_model": result.get("model") or used_config.asr_model,
    } if current['evidence_revision'] == updated['evidence_revision'] else {})
    meta = result.get("transcription_meta") or {}
    # Counts/duration describe processing, never clinical content or credentials.
    metrics = ({"asr_chunk_count": meta["chunk_count"], "asr_completed_chunks": meta["completed_chunks"],
                "audio_duration_seconds": meta["audio_seconds"]} if meta else {})
    _log("transcribed", sid, ok=True, latency_ms=int((time.time() - t0) * 1000),
         transcript_len_bucket=privacy.bucket(len(result["text"])), **metrics)
    return jsonify({"ok": True, "session": _session_view(db.get_session(sid)),
                    "transcription_meta": meta or None})


@bp.put("/api/sessions/<sid>/transcript")
def edit_transcript(sid):
    sess = db.get_session(sid)
    if not sess:
        return jsonify({"ok": False, "message": "会话不存在。"}), 404
    body = request.get_json(silent=True)
    text = body.get("transcript", "") if isinstance(body, dict) else None
    if not isinstance(text, str):
        return jsonify(ok=False, message="逐字稿必须为文本。"), 400
    evidence.change(sid, {"transcript": text})
    _log("transcript_edited", sid, edited=True,
         transcript_len_bucket=privacy.bucket(len(text)))
    return jsonify({"ok": True, "session": _session_view(db.get_session(sid))})


@bp.post("/api/sessions/<sid>/note")
def generate_note(sid):
    sess = db.get_session(sid)
    if not sess:
        return jsonify({"ok": False, "message": "会话不存在。"}), 404
    transcript = sess.get("transcript") or ""
    if not transcript.strip():
        return jsonify({"ok": False, "error_kind": "input", "message": "逐字稿为空，请先转写或手动输入。"}), 400

    db.update_session(sid, status="generating", error_message=None, error_kind=None)
    t0 = time.time()
    used_model = get_config().llm_model
    try:
        note = soap.generate(transcript, history_context.active_text(sess))
    except ConfigError as exc:
        db.update_session(sid, status="failed", error_kind="config", error_message=exc.message)
        _log("note_failed", sid, error_kind="config", ok=False)
        return _err(exc, 503)
    except ProviderError as exc:
        db.update_session(sid, status="failed", error_kind="provider", error_message=exc.message)
        _log("note_failed", sid, error_kind="provider", ok=False)
        return _err(exc, 502)

    try:
        updated = evidence.store_note(sid, note, sess['evidence_revision'], sess.get('note_json'), generated=True)
    except evidence.ChangedDuringRequest:
        return _evidence_conflict()
    if not updated:
        return _evidence_conflict()
    db.mutate_session(sid, lambda current: {"llm_used_model": used_model}
                      if current['note_revision'] == updated['note_revision'] else {})
    summary = grounding.summarize(json.loads(updated['flags_json']))
    _log("note_generated", sid, ok=True, latency_ms=int((time.time() - t0) * 1000),
         flag_count=summary["total"], flag_kinds=list(summary["kinds"].keys()))
    return jsonify({"ok": True, "session": _session_view(db.get_session(sid))})


@bp.put("/api/sessions/<sid>/note")
def edit_note(sid):
    """Any edit re-runs the checker and revokes confirmation."""
    sess = db.get_session(sid)
    if not sess:
        return jsonify({"ok": False, "message": "会话不存在。"}), 404
    body = request.get_json(silent=True)
    incoming = body.get("note") if isinstance(body, dict) else None
    if not isinstance(incoming, dict):
        return jsonify(ok=False, message="病历必须为对象。"), 400
    current_note = json.loads(sess.get("note_json") or '{}')
    if "subjective_sections" in current_note and "subjective_sections" not in incoming:
        return jsonify(ok=False, message="新版病历需分别保存 S 的三个子字段，请刷新页面后重试。"), 400
    try:
        note = soap.normalize_note(incoming)
    except ValueError as exc:
        return jsonify(ok=False, message=str(exc)), 400
    updated = evidence.store_note(sid, note)
    flags = json.loads(updated['flags_json'])
    _log("note_edited", sid, edited=True, flag_count=len(flags))
    return jsonify({"ok": True, "session": _session_view(db.get_session(sid))})


@bp.post("/api/sessions/<sid>/confirm")
def confirm(sid):
    sess = db.get_session(sid)
    if not sess:
        return jsonify({"ok": False, "message": "会话不存在。"}), 404
    if not sess.get("note_json"):
        return jsonify({"ok": False, "error_kind": "input", "message": "还没有病历可确认。"}), 400
    who = ((request.json or {}).get("confirmed_by") or "").strip()
    if not who:
        return jsonify({
            "ok": False, "error_kind": "input",
            "message": "请填写确认人（兽医姓名或工号）。病历必须由具体的人确认。",
        }), 400
    from .patient_records import review_needed
    if review_needed(sess):
        return jsonify(ok=False,message="逐字稿已变化，请先在本次就诊对象卡片重新核对档案与候选差异。"),409
    expected_evidence = (request.json or {}).get('evidence_revision', sess['evidence_revision'])
    expected_note = (request.json or {}).get('note_revision', sess['note_revision'])
    def mark_confirmed(current):
        from .patient_records import review_needed
        if review_needed(current):
            raise evidence.ChangedDuringRequest()
        if (current['evidence_revision'] != expected_evidence or current['note_revision'] != expected_note
                or current.get('note_json') != sess.get('note_json')):
            raise evidence.ChangedDuringRequest()
        return dict(confirmed_at=time.time(), confirmed_by=who, note_needs_review=0)
    try:
        if not db.mutate_session(sid, mark_confirmed):
            return _evidence_conflict()
    except evidence.ChangedDuringRequest:
        return _evidence_conflict()
    _log("note_confirmed", sid, ok=True)
    return jsonify({"ok": True, "session": _session_view(db.get_session(sid))})


@bp.post("/api/sessions/<sid>/unconfirm")
def unconfirm(sid):
    if not db.get_session(sid):
        return jsonify({"ok": False, "message": "会话不存在。"}), 404
    db.update_session(sid, confirmed_at=None, confirmed_by=None)
    return jsonify({"ok": True, "session": _session_view(db.get_session(sid))})


@bp.get("/api/sessions/<sid>/export")
def export(sid):
    """Gated server-side. The UI also hides the buttons, but the gate lives
    here so it cannot be bypassed by calling the API directly."""
    sess = db.get_session(sid)
    if not sess:
        return jsonify({"ok": False, "message": "会话不存在。"}), 404
    if not sess.get("note_json"):
        return jsonify({"ok": False, "error_kind": "input", "message": "还没有病历。"}), 400
    if not sess.get("confirmed_at"):
        return jsonify({
            "ok": False, "error_kind": "not_confirmed",
            "message": "病历尚未经兽医确认，不能复制或导出。请先核对待确认项并确认。",
        }), 409

    from .patient_records import review_needed
    if review_needed(sess):
        return jsonify(ok=False,message="逐字稿已变化，请先重新核对本次就诊对象。"),409
    note = json.loads(sess["note_json"])
    field = request.args.get("field")
    if field is not None:
        if field in dict(soap.SUBJECTIVE_FIELDS):
            sections = note.get("subjective_sections")
            if not isinstance(sections, dict) or field not in sections:
                return jsonify(ok=False, message="旧版 S 尚未拆分，请复制完整 S 原文。"), 400
            text = sections[field]
        elif field in soap.FIELDS:
            text = note.get(field, "未提及")
        else:
            return jsonify(ok=False, message="只能复制病历字段。"), 400
        _log("note_exported", sid, ok=True)
        return jsonify(ok=True, field=field, text=text)
    text = soap.render_text(note)
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(sess["confirmed_at"]))
    text += f"\n\n—— 由 {sess['confirmed_by']} 于 {stamp} 确认"
    _log("note_exported", sid, ok=True)

    if request.args.get("fmt") == "download":
        return Response(
            text, mimetype="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{sid}.txt"'},
        )
    return jsonify({"ok": True, "text": text})


@bp.delete("/api/sessions/<sid>")
def delete_session(sid):
    if not db.delete_session(sid):
        return jsonify({"ok": False, "message": "会话不存在。"}), 404
    _log("session_deleted", sid, ok=True)
    return jsonify({"ok": True})


@bp.delete("/api/sessions/<sid>/<part>")
def delete_part(sid, part):
    if part not in ("audio", "transcript", "note"):
        return jsonify({"ok": False, "message": "只能删除 audio / transcript / note。"}), 400
    if part == "transcript":
        deleted = evidence.change(sid, {"transcript": None})
    else:
        deleted = db.delete_session_part(sid, part)
    if not deleted:
        return jsonify({"ok": False, "message": "会话不存在。"}), 404
    _log("part_deleted", sid, ok=True, part=part)
    return jsonify({"ok": True, "session": _session_view(db.get_session(sid))})


# --- optional history evidence ---------------------------------------------

def _evidence_conflict():
    return jsonify(ok=False, error_kind="conflict", retryable=False,
                   message="处理期间依据或病历已变化，本次结果未覆盖当前内容。请重新打开会话核对后再试。"), 409


@bp.put('/api/sessions/<sid>/history')
def edit_history(sid):
    if not db.get_session(sid):
        return jsonify(ok=False, message="会话不存在。"), 404
    body = request.get_json(silent=True)
    text = body.get('raw') if isinstance(body, dict) else None
    if not isinstance(text, str) or len(text) > history_context.MAX_RAW:
        return jsonify(ok=False, message="既往病历必须为纯文本，且不超过 30000 字。"), 400
    updated = evidence.change(sid, {'history_raw': text})
    return jsonify(ok=True, saved=True, session=_session_view(updated))


@bp.put('/api/sessions/<sid>/history-summary')
def edit_history_summary(sid):
    if not db.get_session(sid):
        return jsonify(ok=False, message="会话不存在。"), 404
    body = request.get_json(silent=True)
    try:
        summary = history_context.normalize_summary(body.get('summary') if isinstance(body, dict) else None)
    except ValueError as exc:
        return jsonify(ok=False, message=str(exc)), 400
    updated = evidence.change(sid, {'history_summary_json':json.dumps(summary, ensure_ascii=False)})
    return jsonify(ok=True, saved=True, session=_session_view(updated))


@bp.post('/api/sessions/<sid>/history-summary')
def generate_history_summary(sid):
    sess = db.get_session(sid)
    if not sess:
        return jsonify(ok=False, message="会话不存在。"), 404
    raw = sess.get('history_raw') or ''
    if not raw.strip():
        return jsonify(ok=False, error_kind='input', message="请先填写既往病历原文。"), 400
    try:
        summary = history_context.generate(raw)
    except ConfigError as exc:
        return _err(exc, 503)
    except ProviderError as exc:
        return _err(exc, 502)
    try:
        updated = evidence.change(sid, {'history_summary_json':json.dumps(summary, ensure_ascii=False)},
                                  expected_revision=sess['evidence_revision'])
    except evidence.ChangedDuringRequest:
        return _evidence_conflict()
    if not updated:
        return _evidence_conflict()
    return jsonify(ok=True, saved=True, session=_session_view(updated))


# --- knowledge assistant ---------------------------------------------------

# Demand-collection copy. Kept here so the wording has one source of truth
# and cannot drift between the API and the page.
REQUEST_INTRO = (
    "知识问答尚未开放。欢迎提交你希望助手解决的问题，"
    "帮助我们确定优先支持的内容；目前不会生成答案。"
)
REQUEST_PLACEHOLDER = "你希望知识助手帮你查什么？"
REQUEST_SUBMIT_LABEL = "提交需求"
REQUEST_SUCCESS = "已收到你的需求，感谢帮助我们确定优先支持的内容。"
REQUEST_PII_WARNING = (
    "请勿填写宠主姓名、联系方式、病例号等可识别信息。"
    "提交的问题仅用于需求分析，用来决定优先支持哪些内容。"
)

PLANNED_SCOPE = [
    "犬猫常见消化道主诉的院内诊疗规范",
    "本院自有处方集中的常用药物条目",
    "集团内部已发布的临床操作 SOP",
]
EXPLICIT_NON_SCOPE = [
    "影像与病理判读",
    "任何形式的诊断结论",
    "知识库覆盖范围之外的病种",
    "未获授权的教材、付费数据库与来源不明的网络资料",
]


@bp.get("/api/kb/status")
def kb_status():
    cfg = get_config()
    body = {
        "ok": True,
        "mode": cfg.kb_mode,
        "planned_scope": PLANNED_SCOPE,
        "non_scope": EXPLICIT_NON_SCOPE,
        "privacy_notice": PRIVACY_NOTICE,
        "request_copy": {
            "intro": REQUEST_INTRO,
            "placeholder": REQUEST_PLACEHOLDER,
            "submit_label": REQUEST_SUBMIT_LABEL,
            "pii_warning": REQUEST_PII_WARNING,
        },
        "retention_days": cfg.log_retention_days,
    }
    if cfg.kb_mode == "rag":
        body["coverage"] = kb_store.coverage()
        body["min_coverage"] = cfg.kb_min_coverage
    return jsonify(body)


@bp.post("/api/kb/ask")
def kb_ask():
    cfg = get_config()
    payload = request.json or {}
    question = (payload.get("question") or "").strip()
    store_raw = bool(payload.get("store_raw"))

    if cfg.kb_mode == "off":
        _log("kb_ask", None, mode="off", refused=True, refuse_reason="off")
        return jsonify({
            "ok": True, "mode": "off", "answerable": False,
            "message": "知识问答已关闭。",
        })

    if cfg.kb_mode == "placeholder":
        _log("kb_ask", None, mode="placeholder", refused=True, refuse_reason="placeholder",
             query_len_bucket=privacy.bucket(len(question)))
        if store_raw and cfg.allow_raw_query_storage:
            scrubbed, counts = privacy.scrub(question)
            db.insert_raw_query(scrubbed, cfg.log_retention_days)
        return jsonify({
            "ok": True, "mode": "placeholder", "answerable": False,
            "message": "知识问答尚未上线。",
            "explanation": "我们不会用模型的泛化知识临时拼一个答案。"
                           "这个功能要等有合法授权、可标注来源的兽医文献入库后才会开放。",
            "planned_scope": PLANNED_SCOPE,
            "non_scope": EXPLICIT_NON_SCOPE,
            "raw_query_stored": bool(store_raw and cfg.allow_raw_query_storage),
            "retention_days": cfg.log_retention_days,
        })

    # rag
    if not question:
        return jsonify({"ok": False, "error_kind": "input", "message": "请输入问题。"}), 400
    t0 = time.time()
    try:
        result = kb_answer.ask(question)
    except ConfigError as exc:
        _log("kb_ask", None, mode="rag", ok=False, error_kind="config")
        return _err(exc, 503)
    except ProviderError as exc:
        _log("kb_ask", None, mode="rag", ok=False, error_kind="provider")
        return _err(exc, 502)

    if store_raw and cfg.allow_raw_query_storage:
        scrubbed, _ = privacy.scrub(question)
        db.insert_raw_query(scrubbed, cfg.log_retention_days)

    _log("kb_ask", None, mode="rag", ok=True,
         latency_ms=int((time.time() - t0) * 1000),
         query_len_bucket=privacy.bucket(len(question)),
         refused=not result["answerable"], refuse_reason=result.get("reason"),
         chunks_matched=len(result.get("sources", [])))
    result.update({"ok": True, "mode": "rag",
                   "raw_query_stored": bool(store_raw and cfg.allow_raw_query_storage),
                   "retention_days": cfg.log_retention_days})
    return jsonify(result)


@bp.post("/api/kb/requests")
def kb_submit_request():
    """Demand collection for the not-yet-open knowledge assistant.

    This never produces an answer. It stores a de-identified record of what
    a vet wanted to look up, so the knowledge base can be built in priority
    order. The success message the UI shows is tied to the row actually
    landing in the table -- if the write fails, the caller is told it failed
    and nothing claims to have been received.
    """
    cfg = get_config()
    text = ((request.json or {}).get("text") or "").strip()

    if not text:
        return jsonify({
            "ok": False, "saved": False, "error_kind": "input",
            "message": "请先写下你希望助手帮你查什么，再提交。",
        }), 400
    if len(text) > 2000:
        return jsonify({
            "ok": False, "saved": False, "error_kind": "input",
            "message": f"内容过长（{len(text)} 字），请压缩到 2000 字以内。",
        }), 400

    scrubbed, counts = privacy.scrub(text)
    try:
        rid = db.insert_feature_request(scrubbed, sorted(counts.keys()),
                                        cfg.log_retention_days)
    except Exception as exc:  # storage failure must not be reported as success
        current_app.logger.exception("feature request write failed")
        return jsonify({
            "ok": False, "saved": False, "error_kind": "storage",
            "message": "提交失败，需求没有保存。请稍后重试。",
            "detail": str(exc)[:200],
        }), 500

    _log("kb_request_submitted", None, mode=cfg.kb_mode, ok=True,
         query_len_bucket=privacy.bucket(len(text)))
    return jsonify({
        "ok": True,
        "saved": True,
        "request_id": rid,
        "message": REQUEST_SUCCESS,
        "scrubbed": sorted(counts.keys()),
        "retention_days": cfg.log_retention_days,
    })


@bp.get("/api/kb/requests")
def kb_list_requests():
    """Operator view. De-identified text only."""
    return jsonify({"ok": True, "count": db.count_feature_requests(),
                    "requests": db.list_feature_requests()})


@bp.get("/api/kb/source/<chunk_id>")
def kb_source(chunk_id):
    """The passage behind a citation, verbatim. This is what makes a citation
    checkable rather than decorative."""
    chunk = db.get_chunk(chunk_id)
    if not chunk:
        return jsonify({"ok": False, "message": "引用对应的原文片段不存在（文档可能已下线）。"}), 404
    return jsonify({
        "ok": True,
        "chunk_id": chunk["id"], "text": chunk["text"], "locator": chunk["locator"],
        "title": chunk["title"], "source": chunk["source"],
        "version": chunk["version"], "published_on": chunk["published_on"],
        "license_note": chunk["license_note"],
    })


@bp.get("/api/kb/docs")
def kb_docs():
    return jsonify({"ok": True, "documents": db.list_docs(active_only=False)})


@bp.post("/api/kb/docs/<doc_id>/active")
def kb_doc_active(doc_id):
    active = bool((request.json or {}).get("active", True))
    if not db.set_doc_active(doc_id, active):
        return jsonify({"ok": False, "message": "文档不存在。"}), 404
    return jsonify({"ok": True})


@bp.delete("/api/kb/docs/<doc_id>")
def kb_doc_delete(doc_id):
    if not db.delete_doc(doc_id):
        return jsonify({"ok": False, "message": "文档不存在。"}), 404
    return jsonify({"ok": True})


# --- privacy ---------------------------------------------------------------

@bp.get("/api/privacy")
def privacy_info():
    cfg = get_config()
    return jsonify({
        "ok": True,
        "notice": PRIVACY_NOTICE,
        "retention_days": cfg.log_retention_days,
        "raw_query_storage_enabled": cfg.allow_raw_query_storage,
        "what_is_logged": [
            "事件类型与时间戳",
            "耗时毫秒数",
            "文本长度区间（不是文本本身）",
            "核对项数量与类型",
            "是否被拒答及拒答原因",
            "加盐哈希后的会话引用（无法反推会话 ID）",
        ],
        "what_is_not_logged": ["音频", "逐字稿", "病历正文", "姓名、电话、病例号等标识信息"],
        "how_to_delete": {
            "single_session": "DELETE /api/sessions/<id>",
            "one_part": "DELETE /api/sessions/<id>/{audio|transcript|note}",
            "expired_logs": "POST /api/privacy/purge",
            "all_raw_queries": "DELETE /api/privacy/raw-queries",
            "all_feature_requests": "DELETE /api/privacy/feature-requests",
        },
    })


@bp.post("/api/privacy/purge")
def privacy_purge():
    return jsonify({"ok": True, **db.purge_expired()})


@bp.delete("/api/privacy/feature-requests")
def privacy_delete_requests():
    return jsonify({"ok": True, "deleted": db.delete_all_feature_requests()})


@bp.delete("/api/privacy/raw-queries")
def privacy_delete_raw():
    return jsonify({"ok": True, "deleted": db.delete_all_raw_queries()})
