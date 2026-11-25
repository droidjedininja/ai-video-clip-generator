import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from services import whisper, highlights


def test_transcription_worker_preserves_times_and_has_timeout(monkeypatch, tmp_path):
    transcript = {'text': 'speech', 'segments': [{'start': 0, 'end': 2.5, 'text': 'speech'}]}
    runner = Mock(return_value=SimpleNamespace(stdout=json.dumps(transcript)))
    monkeypatch.setattr(whisper.subprocess, 'run', runner)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    assert whisper.transcribe_audio(str(tmp_path/'audio.mp3')) == transcript
    assert runner.call_args.kwargs['timeout'] == 600
    assert runner.call_args.args[0][1:3] == ['-m', 'services.whisper']


@pytest.mark.parametrize('failure', [subprocess.CalledProcessError(1, 'worker'), OSError('missing worker')])
def test_worker_failure_is_explicit(monkeypatch, failure):
    monkeypatch.setattr(whisper.subprocess, 'run', Mock(side_effect=failure))
    with pytest.raises(whisper.LocalTranscriptionError):
        whisper.transcribe_audio('/tmp/audio.mp3')


def test_worker_timeout_propagates(monkeypatch):
    monkeypatch.setattr(whisper.subprocess, 'run', Mock(side_effect=subprocess.TimeoutExpired('worker', 600)))
    with pytest.raises(subprocess.TimeoutExpired):
        whisper.transcribe_audio('/tmp/audio.mp3')


@pytest.mark.parametrize('timeout', ['nan', '0', '-1', '3601', 'inf'])
def test_invalid_timeout_never_starts_worker(monkeypatch, timeout):
    monkeypatch.setenv('WHISPER_TIMEOUT_SECONDS', timeout)
    runner = Mock()
    monkeypatch.setattr(whisper.subprocess, 'run', runner)
    with pytest.raises(whisper.LocalTranscriptionError):
        whisper.transcribe_audio('/tmp/audio.mp3')
    runner.assert_not_called()


def test_local_engine_is_cpu_int8_and_supports_offline_cache(monkeypatch, tmp_path):
    segments = [SimpleNamespace(start=0, end=2.5, text=' speech ', no_speech_prob=.1),
                SimpleNamespace(start=3, end=5, text='silence hallucination', no_speech_prob=.9)]
    model = Mock()
    model.transcribe.return_value = (iter(segments), None)
    factory = Mock(return_value=model)
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=factory))
    monkeypatch.setenv('WHISPER_CACHE_DIR', str(tmp_path))
    monkeypatch.setenv('WHISPER_OFFLINE', '1')
    monkeypatch.setenv('WHISPER_MODEL', 'tiny')
    assert whisper.run_local('audio.mp3') == {'text': 'speech', 'segments': [{'start': 0, 'end': 2.5, 'text': 'speech'}]}
    assert factory.call_args.kwargs == {'device':'cpu', 'compute_type':'int8', 'cpu_threads':2,
                                       'num_workers':1, 'download_root':str(tmp_path), 'local_files_only':True}
    assert model.transcribe.call_args.kwargs['vad_filter'] is True


def test_unknown_model_rejected_before_loading(monkeypatch):
    factory = Mock()
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=factory))
    monkeypatch.setenv('WHISPER_MODEL', 'arbitrary/remote-model')
    with pytest.raises(ValueError):
        whisper.run_local('audio.mp3')
    factory.assert_not_called()


def transcript(*segments):
    return {'text': 'sample', 'segments': [{'start': start, 'end': end, 'text': text} for start, end, text in segments]}


def test_rules_are_deterministic_nonoverlapping_and_bounded():
    data = transcript((0,12,'Here is an introduction.'), (12,30,'Why does this work? Because practice is important.'),
                      (40,60,'Remember this useful tip and learn how to improve.'),
                      (70,90,'Another clear idea with a useful explanation.'), (100,120,'A final thought.'))
    selected = highlights.detect_highlights(data, 120)
    assert selected == highlights.detect_highlights(data, 120)
    assert 1 <= len(selected) <= 3
    assert all(0 <= h['start'] < h['end'] <= 120 and h['end']-h['start'] <=45 for h in selected)
    assert all(a['end'] <= b['start'] for a,b in zip(selected, selected[1:]))
    assert all(h['reason'].startswith('Rule-based:') for h in selected)


def test_question_explanation_cues_win_equal_duration():
    data = transcript((0,10,'plain introduction words to begin this recording today'),
                      (20,30,'Why learn this important tip? Because mistakes matter.'))
    selected = highlights.detect_highlights(data, 30)
    assert any('question/explanation' in h['reason'] for h in selected)


def test_end_timestamp_rounding_and_short_video():
    selected = highlights.detect_highlights(transcript((0,2.5,'A short idea.')), 2)
    assert selected[0]['start'] == 0
    assert selected[0]['end'] == 2


@pytest.mark.parametrize('data', [transcript(), transcript((0,4,'')), transcript((8,10,'past the video')),
    transcript((-1,3,'negative')), transcript((3,1,'reversed')),
    transcript((True,4,'boolean')), transcript((float('nan'),4,'not finite')),
    transcript((0,float('inf'),'not finite'))])
def test_silent_or_invalid_transcripts_have_no_fake_highlights(data):
    with pytest.raises(highlights.InvalidHighlights):
        highlights.detect_highlights(data, 5)


def test_one_long_segment_is_capped():
    selected = highlights.detect_highlights(transcript((0,120,'A long spoken idea.')), 120)
    assert selected[0]['end'] == 45
