from bucket_scoring import calc_topic_score


def test_followup_section_does_not_create_topic_relevance() -> None:
    with_followup = {
        "metadata": {"name": "", "domain": [], "tags": []},
        "content": "ordinary memory\n\n### followup\nprivate-reminder-token",
    }
    without_followup = {
        "metadata": {"name": "", "domain": [], "tags": []},
        "content": "ordinary memory",
    }

    assert calc_topic_score("private-reminder-token", with_followup) == (
        calc_topic_score("private-reminder-token", without_followup)
    )


def test_memory_body_still_creates_topic_relevance() -> None:
    bucket = {
        "metadata": {"name": "", "domain": [], "tags": []},
        "content": "private-reminder-token\n\n### followup\nother task",
    }

    assert calc_topic_score("private-reminder-token", bucket) > 0.0
