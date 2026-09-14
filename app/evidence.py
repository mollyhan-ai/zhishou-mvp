"""Atomically invalidate derived state whenever an evidence source changes."""
import json
import time
from . import db, grounding, history_context

class ChangedDuringRequest(Exception):
    pass


def change(sid, changes, expected_revision=None):
    def mutate(sess):
        if expected_revision is not None and sess['evidence_revision'] != expected_revision:
            raise ChangedDuringRequest()
        fields = dict(changes)
        if 'history_raw' in fields:
            if fields['history_raw'] == (sess.get('history_raw') or ''):
                fields.pop('history_raw')
            else:
                old_summary = json.loads(sess.get('history_summary_json') or '{}')
                fields['history_summary_stale'] = int(bool(history_context.summary_text(old_summary)))
                if not fields['history_raw'].strip():
                    fields.update(history_summary_json='{}', history_summary_stale=0)
        if 'history_summary_json' in fields:
            fields['history_summary_stale'] = 0
        fields = {k:v for k,v in fields.items() if v != sess.get(k)}
        if not fields:
            return {}
        revised = {**sess, **fields}
        fields.update(evidence_revision=sess['evidence_revision'] + 1,
                      confirmed_at=None, confirmed_by=None,
                      note_needs_review=int(bool(sess.get('note_json'))),
                      error_message=None, error_kind=None,
                      status='transcribed' if (revised.get('transcript') or '').strip() else 'created')
        if 'transcript' in changes:
            fields['transcript_edited_at'] = time.time()
            fields['status'] = 'transcribed' if (revised.get('transcript') or '').strip() else 'created'
            # A proposal is only reviewable against the exact transcript from
            # which it was extracted. Confirmed patient records are untouched.
            fields.update(patient_candidate_json='{}', patient_candidate_hash=None,
                          patient_candidate_at=None)
        if sess.get('note_json'):
            flags = grounding.check(revised.get('transcript') or '', json.loads(sess['note_json']),
                                    history_context.active_text(revised))
            fields.update(flags_json=json.dumps(flags, ensure_ascii=False), status='drafted')
        return fields
    return db.mutate_session(sid, mutate)


def store_note(sid, note, expected_revision=None, expected_note=None, generated=False):
    def mutate(sess):
        if expected_revision is not None and (sess['evidence_revision'] != expected_revision or sess.get('note_json') != expected_note):
            raise ChangedDuringRequest()
        flags = grounding.check(sess.get('transcript') or '', note, history_context.active_text(sess))
        return dict(note_json=json.dumps(note, ensure_ascii=False), note_revision=sess['note_revision'] + 1,
                    flags_json=json.dumps(flags, ensure_ascii=False),
                    **({'note_generated_at':time.time()} if generated else {'note_edited_at':time.time()}),
                    confirmed_at=None, confirmed_by=None, note_needs_review=0,
                    status='drafted', error_message=None, error_kind=None)
    return db.mutate_session(sid, mutate)
