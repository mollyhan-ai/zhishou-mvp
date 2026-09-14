import io
import time
import wave
from unittest.mock import patch
from tests.base import AppTestCase
from app import db


def wav(seconds=1.25):
    stream=io.BytesIO()
    with wave.open(stream,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\x00\x00'*int(seconds*16000))
    return stream.getvalue()


class AudioMetadataTests(AppTestCase):
    def upload(self,sid,**metadata):
        return self.client.post('/api/sessions/'+sid+'/audio',data={'file':(io.BytesIO(wav()),'recording.wav'),**metadata},content_type='multipart/form-data')
    def test_recording_start_and_file_duration_persist(self):
        sid=self.new_session();started=time.time()-5
        r=self.upload(sid,audio_origin='recording',recorded_at=str(started));self.assertEqual(r.status_code,200)
        s=r.json['session'];self.assertEqual(s['audio_origin'],'recording');self.assertEqual(s['recorded_at'],started);self.assertEqual(s['audio_duration_seconds'],1.25)
        self.assertGreater(s['audio_uploaded_at'],started)
        reopened=self.client.get('/api/sessions/'+sid).json['session'];self.assertEqual(reopened['audio_duration_seconds'],1.25);self.assertEqual(reopened['recorded_at'],started)
    def test_uploaded_file_does_not_invent_recording_time(self):
        sid=self.new_session();r=self.upload(sid);s=r.json['session']
        self.assertEqual(s['audio_origin'],'upload');self.assertIsNone(s['recorded_at']);self.assertIsNotNone(s['audio_uploaded_at'])
    def test_invalid_timestamp_rejected_before_overwriting(self):
        sid=self.new_session();self.upload(sid);before=db.get_session(sid)
        for timestamp in ('nan','inf','not-a-time','-1',str(time.time()+600)):
            r=self.upload(sid,audio_origin='recording',recorded_at=timestamp);self.assertEqual(r.status_code,400);self.assertEqual(db.get_session(sid),before)
    def test_legacy_audio_duration_computed_without_mutation(self):
        sid=self.new_session();self.upload(sid);db.update_session(sid,audio_origin=None,recorded_at=None,audio_uploaded_at=None,audio_duration_seconds=None)
        before=db.get_session(sid);s=self.client.get('/api/sessions/'+sid).json['session']
        self.assertEqual(s['audio_duration_seconds'],1.25);self.assertIsNone(s['recorded_at']);self.assertEqual(before,db.get_session(sid))
    def test_unreadable_audio_metadata_is_unknown(self):
        sid=self.new_session();r=self.attach_audio(sid);self.assertEqual(r.status_code,200);self.assertIsNone(r.json['session']['audio_duration_seconds'])
    def test_language_is_setting_used_for_transcription(self):
        sid=self.new_session();self.upload(sid)
        with patch('app.routes.asr_provider.transcribe',return_value={'text':'测试逐字稿。','model':'test'}):r=self.client.post('/api/sessions/'+sid+'/transcribe')
        self.assertEqual(r.status_code,200);self.assertEqual(r.json['session']['asr_used_language'],'zh')
        self.assertEqual(self.client.get('/api/status').json['asr_language'],'zh')
    def test_remove_audio_clears_only_its_metadata(self):
        sid=self.new_session();self.upload(sid,audio_origin='recording',recorded_at=str(time.time()-5));self.set_transcript(sid,'保留逐字稿。')
        s=self.client.delete('/api/sessions/'+sid+'/audio').json['session']
        self.assertEqual(s['transcript'],'保留逐字稿。')
        for k in ('recorded_at','audio_duration_seconds','audio_uploaded_at','audio_origin'):self.assertIsNone(s[k])
    def test_existing_confirmation_and_clinical_fields_not_changed_by_metadata(self):
        sid=self.new_session();db.update_session(sid,note_json='{"subjective":"旧病历"}',confirmed_at=123,confirmed_by='兽医',evidence_revision=4,note_revision=6)
        self.upload(sid);s=db.get_session(sid)
        self.assertEqual(s['confirmed_at'],123);self.assertEqual(s['evidence_revision'],4);self.assertEqual(s['note_revision'],6)
