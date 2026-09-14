import base64
import io
import json
import wave
from unittest import mock

from tests.base import AppTestCase
from app import db
from app.providers import ProviderError
from app.providers.doubao import split_wav


def wav(seconds=92.1, channels=1, value=1024):
    output = io.BytesIO()
    with wave.open(output, 'wb') as f:
        f.setnchannels(channels); f.setsampwidth(2); f.setframerate(1000)
        f.writeframes(value.to_bytes(2, 'little', signed=True) * channels * round(seconds * 1000))
    return output.getvalue()


def response(text='片段文字', finish='stop'):
    return mock.Mock(status_code=200, json=lambda: {'choices': [{'message': {'content': text}, 'finish_reason': finish}]})


class ChunkTests(AppTestCase):
    def setUp(self):
        super().setUp()
        from app.config import get_config
        get_config().asr_provider = 'doubao'
        self.sid = self.new_session()
        self.attach_audio(self.sid, data=wav())

    def call(self):
        return self.client.post('/api/sessions/' + self.sid + '/transcribe')

    def test_92_seconds_four_parts_cover_every_frame_once_in_both_channel_layouts(self):
        for channels in (1, 2):
            raw = wav(channels=channels)
            parts = split_wav(raw)
            self.assertEqual(len(parts), 4)
            assembled = b''; previous_end = 0
            for data, start, end, silence in parts:
                self.assertEqual(start, previous_end)
                self.assertGreater(end, start); self.assertLessEqual(end - start, 30)
                previous_end = end
                with wave.open(io.BytesIO(data), 'rb') as f:
                    self.assertEqual(f.getnchannels(), channels)
                    assembled += f.readframes(f.getnframes())
            self.assertEqual(previous_end, 92.1)
            with wave.open(io.BytesIO(raw), 'rb') as f:
                self.assertEqual(assembled, f.readframes(f.getnframes()))

    def test_sustained_pause_preferred_over_brief_valley_inside_word(self):
        content = bytearray((1024).to_bytes(2, 'little') * 50_000)
        content[24_400 * 2:24_800 * 2] = b'\0' * 800
        content[24_990 * 2:25_010 * 2] = b'\0' * 40
        output = io.BytesIO()
        with wave.open(output, 'wb') as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(1000); f.writeframes(content)
        parts = split_wav(output.getvalue())
        self.assertGreater(parts[0][2], 24.4)
        self.assertLess(parts[0][2], 24.8)

    def test_all_results_saved_in_order_without_deduplicating_repeated_words(self):
        replies = ['无呕吐。', '无呕吐。', '复查记录。', '最后一段不能丢。']
        with mock.patch('app.providers.doubao.requests.post', side_effect=[response(t) for t in replies]) as post:
            result = self.call()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(db.get_session(self.sid)['transcript'], '\n'.join(replies))
        self.assertEqual(post.call_count, 4)
        for call in post.call_args_list:
            raw = base64.b64decode(call.kwargs['json']['messages'][1]['content'][0]['input_audio']['data'])
            with wave.open(io.BytesIO(raw), 'rb') as f:self.assertLessEqual(f.getnframes()/f.getframerate(), 30)
        meta = result.json['transcription_meta']
        self.assertEqual(meta['completed_chunks'], 4); self.assertEqual(meta['audio_seconds'], 92.1)
        with db.connect() as con:
            log = con.execute("SELECT payload FROM event_log WHERE event='transcribed'").fetchone()[0]
        self.assertNotIn(replies[-1], log)
        self.assertEqual(json.loads(log)['asr_completed_chunks'], 4)

    def test_failure_on_last_part_preserves_original_evidence_note_and_confirmation(self):
        self.set_transcript(self.sid, '人工修订的原文')
        db.update_session(self.sid, note_json='{"subjective":"原病历"}', flags_json='[]', confirmed_at=1, confirmed_by='医生')
        before = db.get_session(self.sid)
        with mock.patch('app.providers.doubao.requests.post', side_effect=[response('新片段')] * 3 + [response('截断', 'length')]):
            result = self.call()
        self.assertEqual(result.status_code, 502); self.assertIn('4/4', result.json['message'])
        after = db.get_session(self.sid)
        for key in ('transcript', 'note_json', 'flags_json', 'confirmed_at', 'confirmed_by', 'evidence_revision'):
            self.assertEqual(before[key], after[key], key)

    def test_no_speech_on_nonzero_audio_fails_instead_of_omitting_part(self):
        with mock.patch('app.providers.doubao.requests.post', side_effect=[response('前段'), response('[未识别到语音]')]) as post:
            result = self.call()
        self.assertEqual(result.status_code, 502); self.assertEqual(post.call_count, 2)
        self.assertFalse(db.get_session(self.sid)['transcript'])

    def test_exact_digital_silence_may_be_empty_but_other_parts_are_preserved(self):
        self.attach_audio(self.sid, data=wav(value=0))
        with mock.patch('app.providers.doubao.requests.post', side_effect=[response('测试文字')] + [response('[未识别到语音]')] * 3):
            result = self.call()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['session']['transcript'], '测试文字')
        self.assertTrue(result.json['transcription_meta']['input_ranges'][-1]['blank'])

    def test_total_deadline_stops_remaining_parts_without_partial_save(self):
        with mock.patch('app.providers.doubao.time.monotonic', side_effect=[0, 1, 181]), mock.patch('app.providers.doubao.requests.post', return_value=response()) as post:
            result = self.call()
        self.assertEqual(result.status_code, 502); self.assertEqual(post.call_count, 1)
        self.assertFalse(db.get_session(self.sid)['transcript'])

    def test_incomplete_wav_and_overlength_rejected_before_network(self):
        for content in (wav()[:-100], wav(seconds=600.1)):
            self.attach_audio(self.sid, data=content)
            with mock.patch('app.providers.doubao.requests.post') as post:
                self.assertEqual(self.call().status_code, 502)
            post.assert_not_called()

    def test_new_manual_edit_during_transcription_is_not_overwritten(self):
        from app import evidence
        called = 0
        def reply(*args, **kwargs):
            nonlocal called
            called += 1
            if called == 4:evidence.change(self.sid, {'transcript':'处理期间的人工修改'})
            return response('模型文字')
        with mock.patch('app.providers.doubao.requests.post', side_effect=reply): result = self.call()
        self.assertEqual(result.status_code, 409)
        self.assertEqual(db.get_session(self.sid)['transcript'], '处理期间的人工修改')
