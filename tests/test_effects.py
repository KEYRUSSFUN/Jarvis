import numpy as np

from jarvis.audio.effects import VoiceEffects

SR = 48000


def speech_like(seconds: float = 0.5) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return (0.5 * np.sin(2 * np.pi * 120 * t) * np.sin(2 * np.pi * 3 * t)).astype(np.float32)


def test_without_effects_audio_is_unchanged():
    audio = speech_like()
    assert np.array_equal(VoiceEffects(SR, metal=0, room=0)(audio), audio)


def test_metal_adds_quieter_copies_within_a_few_milliseconds():
    click = np.zeros(SR // 10, dtype=np.float32)
    click[0] = 1.0
    out = VoiceEffects(SR, metal=0.5, room=0)(click)
    echoes = np.flatnonzero(out)[1:]
    assert len(echoes) >= 2
    assert echoes.max() < SR * 0.005
    assert np.all(np.abs(out[echoes]) < out[0])


def test_room_leaves_an_echo_tail_after_the_phrase():
    audio = speech_like()
    out = VoiceEffects(SR, metal=0, room=0.3)(audio)
    assert len(out) > len(audio) + SR * 0.2
    assert np.abs(out[len(audio) :]).max() > 0.01


def test_effects_keep_loudness_and_do_not_clip():
    audio = speech_like()
    out = VoiceEffects(SR, metal=0.35, room=0.25)(audio)
    assert out.dtype == np.float32
    assert np.isclose(np.abs(out).max(), np.abs(audio).max(), rtol=1e-3)


def test_sound_shorter_than_metal_delay_does_not_break():
    out = VoiceEffects(SR, metal=0.35, room=0.25)(np.full(10, 0.1, dtype=np.float32))
    assert np.isfinite(out).all()


def test_effects_sound_the_same_every_time():
    audio = speech_like()
    effects = VoiceEffects(SR, metal=0.35, room=0.25)
    assert np.array_equal(effects(audio), VoiceEffects(SR, metal=0.35, room=0.25)(audio))
