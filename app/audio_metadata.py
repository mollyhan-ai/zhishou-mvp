"""Audio metadata is measured from the file, never inferred from transcript text."""
import math
import wave
import struct


def duration(path):
    if not path:
        return None
    try:
        with wave.open(str(path), 'rb') as audio:
            rate = audio.getframerate()
            seconds = audio.getnframes() / rate if rate else None
            return seconds if seconds is not None and math.isfinite(seconds) and seconds > 0 else None
    except (OSError, EOFError, wave.Error, struct.error):
        return None


def upload_fields(form, now):
    origin = form.get('audio_origin', 'upload')
    if origin not in ('recording', 'upload'):
        raise ValueError('音频来源格式不正确。')
    started = None
    if origin == 'recording':
        try:
            started = float(form.get('recorded_at', ''))
        except (TypeError, ValueError):
            raise ValueError('未收到有效录制时间，请重新录音。') from None
        if not math.isfinite(started) or not 0 < started <= now + 300:
            raise ValueError('录制时间不正确，请检查设备时间。')
    return {'audio_origin':origin, 'recorded_at':started, 'audio_uploaded_at':now}


def view(sess):
    return {
        'audio_origin':sess.get('audio_origin'),
        'recorded_at':sess.get('recorded_at'),
        'audio_uploaded_at':sess.get('audio_uploaded_at'),
        'audio_duration_seconds':sess.get('audio_duration_seconds') if sess.get('audio_duration_seconds') is not None else duration(sess.get('audio_path')),
        'asr_used_language':sess.get('asr_used_language'),
    }
