import math
import re


class InvalidHighlights(ValueError):
    pass


def validate_highlights(value, duration):
    if not isinstance(value, dict) or not isinstance(value.get('highlights'), list):
        raise InvalidHighlights('Expected a highlights list')
    highlights = value['highlights']
    if not 1 <= len(highlights) <= 3:
        raise InvalidHighlights('Expected one to three highlights')
    for h in highlights:
        if not isinstance(h, dict):
            raise InvalidHighlights('Invalid highlight')
        start, end = h.get('start'), h.get('end')
        if any(isinstance(t, bool) or not isinstance(t, (int, float)) or (isinstance(t, float) and not math.isfinite(t)) for t in (start, end)):
            raise InvalidHighlights('Times must be finite numbers')
        if not 0 <= start < end <= duration:
            raise InvalidHighlights('Highlight lies outside the video duration')
        if not isinstance(h.get('reason'), str) or not h['reason'].strip():
            raise InvalidHighlights('Highlight needs an explanation')
    return highlights


def detect_highlights(transcript, duration):
    """Rank speech windows by density and question/explanation cues, without a model."""
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise InvalidHighlights('Invalid video duration')
    segments = []
    for segment in transcript.get('segments', []):
        if not isinstance(segment, dict):
            raise InvalidHighlights('Invalid transcript segment')
        start, end, text = segment.get('start'), segment.get('end'), segment.get('text')
        if any(isinstance(t, bool) or not isinstance(t, (int, float)) or
               (isinstance(t, float) and not math.isfinite(t)) for t in (start, end)):
            raise InvalidHighlights('Invalid transcript timestamps')
        if start < 0 or end <= start or not isinstance(text, str):
            raise InvalidHighlights('Invalid transcript segment')
        # Whisper can round the last segment beyond the media's measured duration.
        end = min(end, duration)
        if start < end and text.strip():
            segments.append((start, end, text.strip()))
    segments.sort(key=lambda s: (s[0], s[1]))
    candidates = []
    cues = {'how', 'why', 'because', 'tip', 'important', 'remember', 'mistake', 'learn'}
    for i, (start, first_end, first_text) in enumerate(segments):
        end, texts = min(first_end, start + 45), [first_text]
        for next_start, next_end, next_text in segments[i+1:]:
            if next_start - end > 3 or next_end - start > 45 or first_end - start > 45:
                break
            end = max(end, next_end)
            texts.append(next_text)
        length = end - start
        if length < min(3, duration):
            continue
        text = ' '.join(texts)
        words = re.findall(r"\b[\w']+\b", text.lower())
        density = min(len(words) / length, 3) / 3
        cue_count = len(cues.intersection(words)) + int('?' in text)
        score = density + min(cue_count, 3) * .25 + min(length / 20, 1) * .25
        reason = 'Rule-based: timed speech window, ranked by speech density'
        if cue_count:
            reason += ' and question/explanation cues'
        candidates.append((score, {'start': start, 'end': end, 'reason': reason}))
    selected = []
    for _, candidate in sorted(candidates, key=lambda item: (-item[0], item[1]['start'])):
        if any(candidate['start'] < prior['end'] and candidate['end'] > prior['start'] for prior in selected):
            continue
        selected.append(candidate)
        if len(selected) == 3:
            break
    if not selected:
        raise InvalidHighlights('No usable timed speech windows were found')
    return validate_highlights({'highlights': sorted(selected, key=lambda h: h['start'])}, duration)
