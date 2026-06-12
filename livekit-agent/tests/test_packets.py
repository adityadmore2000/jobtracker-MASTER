import json

from agent import (
    FINAL_TRANSCRIPT_TYPE,
    TRANSCRIPTION_ERROR_TYPE,
    UTTERANCE_END_TYPE,
    UTTERANCE_START_TYPE,
    build_final_transcript_payload,
    build_transcription_error_payload,
    parse_utterance_control_packet,
)


def test_valid_utterance_start_packet_is_accepted():
    packet, warning = parse_utterance_control_packet(b'{"type":"utterance_start","utterance_id":"abc-123"}')

    assert warning is None
    assert packet is not None
    assert packet.packet_type == UTTERANCE_START_TYPE
    assert packet.utterance_id == "abc-123"


def test_valid_utterance_end_packet_is_accepted():
    packet, warning = parse_utterance_control_packet(b'{"type":"utterance_end","utterance_id":"abc-123"}')

    assert warning is None
    assert packet is not None
    assert packet.packet_type == UTTERANCE_END_TYPE
    assert packet.utterance_id == "abc-123"


def test_invalid_utf8_is_ignored_safely():
    packet, warning = parse_utterance_control_packet(b"\xff\xfe")
    assert packet is None
    assert warning == "invalid_utf8"


def test_invalid_json_is_ignored_safely():
    packet, warning = parse_utterance_control_packet(b"{not-json")
    assert packet is None
    assert warning == "invalid_json"


def test_unknown_type_is_ignored_safely():
    packet, warning = parse_utterance_control_packet(b'{"type":"other","utterance_id":"abc"}')
    assert packet is None
    assert warning == "unknown_type"


def test_missing_or_empty_utterance_id_is_ignored_safely():
    packet, warning = parse_utterance_control_packet(b'{"type":"utterance_end"}')
    assert packet is None
    assert warning == "missing_utterance_id"

    packet, warning = parse_utterance_control_packet(b'{"type":"utterance_end","utterance_id":"   "}')
    assert packet is None
    assert warning == "empty_utterance_id"


def test_result_payloads_are_valid_utf8_json():
    final_payload = build_final_transcript_payload("u1", "hello")
    error_payload = build_transcription_error_payload("u2", "bad")

    assert json.loads(final_payload.decode("utf-8")) == {
        "type": FINAL_TRANSCRIPT_TYPE,
        "utterance_id": "u1",
        "text": "hello",
    }
    assert json.loads(error_payload.decode("utf-8")) == {
        "type": TRANSCRIPTION_ERROR_TYPE,
        "utterance_id": "u2",
        "message": "bad",
    }
