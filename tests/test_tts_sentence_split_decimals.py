"""
Streaming TTS sentence split must not cut a decimal in half.

Measured 2026-11-05: a streamed reply containing "$0.79/hr" was split while
the buffer ended at "$0." (the digits were still in the next delta), so the
next TTS sentence started "79/hr on-demand ...". A period that ends the
buffer right after a digit is not a boundary yet.
"""

from routes.conversation import extract_tts_sentence

HEAD = 'GPU rentals from the first provider cost $0.'


def test_buffer_ending_in_digit_period_waits_for_more_text():
    assert extract_tts_sentence(HEAD, min_len=10) == (None, HEAD)


def test_decimal_completed_by_next_delta_stays_in_one_sentence():
    buf = HEAD + '79/hr on demand. Next one'
    sentence, rest = extract_tts_sentence(buf, min_len=10)
    assert sentence == 'GPU rentals from the first provider cost $0.79/hr on demand.'
    assert rest == 'Next one'


def test_simulated_stream_never_starts_a_sentence_with_decimal_digits():
    reply = ('Research is done. GPUwerk charges $0.79/hr on demand, and '
             'Enverge charges $0.75 to $0.85/hr. The cheapest is $0.50/hr. ')
    deltas = ['Research is done. GPUwerk charges $0.', '79/hr on demand, and ',
              'Enverge charges $0.', '75 to $0.', '85/hr. The cheapest is $0.',
              '50/hr. ']
    assert ''.join(deltas) == reply
    buf, spoken = '', []
    for d in deltas:
        buf += d
        while True:
            sentence, buf = extract_tts_sentence(buf, min_len=10)
            if not sentence:
                break
            spoken.append(sentence)
    if buf.strip():
        spoken.append(buf.strip())
    assert spoken == ['Research is done.',
                      'GPUwerk charges $0.79/hr on demand, and Enverge charges $0.75 to $0.85/hr.',
                      'The cheapest is $0.50/hr.']


def test_ordinary_boundaries_unchanged():
    assert extract_tts_sentence('Hello there, how are you today. More', min_len=10) == \
        ('Hello there, how are you today.', 'More')
    assert extract_tts_sentence('Is that the one you meant?', min_len=10) == \
        ('Is that the one you meant?', '')
    # A sentence ending in a number followed by a space is still a boundary.
    assert extract_tts_sentence('The total came to 42. Next', min_len=10) == \
        ('The total came to 42.', 'Next')


def test_min_len_respected():
    assert extract_tts_sentence('Hi. Short.', min_len=40) == (None, 'Hi. Short.')
