from boardmatch import (
    ApplicationStage,
    Remuneration,
    coach,
    discovery,
    network,
    profiles,
)
from boardmatch.fit import rank, score_opportunity
from boardmatch.readiness import ReadinessTracker


def candidate():
    return profiles.load_sample_candidate()


def test_discovery_aggregates_sources():
    opportunities = discovery.discover()
    assert len(opportunities) >= 10
    sources = {o.source for o in opportunities}
    assert any("Government" in s for s in sources)
    assert any("mocked" in s for s in sources)


def test_paid_only_filter_excludes_voluntary_and_unknown():
    paid = discovery.discover(paid_only=True)
    assert paid
    assert all(o.remuneration is Remuneration.PAID for o in paid)
    assert all(o.is_paid for o in paid)


def test_min_fee_and_sector_filters():
    assert all(o.fee_aud >= 100000 for o in discovery.discover(min_fee_aud=100000))
    assert all(o.sector == "Health" for o in discovery.discover(sector="health"))


def test_get_opportunity():
    assert (
        discovery.get_opportunity("gov-001").organisation
        == "Australian Digital Health Agency"
    )
    assert discovery.get_opportunity("nope") is None


def test_fit_scores_and_names_gaps():
    person = candidate()
    opportunity = discovery.get_opportunity("gov-002")
    fit = score_opportunity(person, opportunity)
    assert 0 <= fit.score <= 100
    assert "audit committee" in fit.missing_required
    assert "infrastructure" in fit.missing_desirable
    assert any("audit" in action.lower() for action in fit.gap_actions)
    assert any("Company Directors Course" in action for action in fit.gap_actions)


def test_fit_ranking_is_descending():
    fits = rank(candidate(), discovery.discover())
    assert [f.score for f in fits] == sorted((f.score for f in fits), reverse=True)


def test_director_course_lifts_score():
    baseline = candidate()
    upskilled = candidate()
    upskilled.credentials.append("GAICD (AICD Company Directors Course)")
    opportunity = discovery.get_opportunity("asx-101")
    assert (
        score_opportunity(upskilled, opportunity).score
        > score_opportunity(baseline, opportunity).score
    )


def test_network_finds_direct_board_seat_path():
    person = candidate()
    opportunity = discovery.get_opportunity("asx-101")
    path = network.best_path(person, opportunity)
    assert path is not None
    assert path.connection.name == "Alex Chen"
    assert path.warmth >= 60


def test_network_returns_ranked_paths():
    paths = network.paths_for(candidate(), discovery.get_opportunity("gov-001"))
    assert [p.warmth for p in paths] == sorted((p.warmth for p in paths), reverse=True)


def test_coach_drafts_use_template_engine_offline():
    person = candidate()
    opportunity = discovery.get_opportunity("gov-003")
    fit = score_opportunity(person, opportunity)

    cv = coach.board_cv(person, fit)
    assert cv.engine == "template"
    assert "Governance experience" in cv.content
    assert person.name in cv.content

    bio = coach.director_bio(person)
    assert person.name in bio.content

    outreach = coach.outreach_message(person, opportunity, intro_via="Dr Helen Osei")
    assert "Dr Helen Osei" in outreach.content
    assert opportunity.organisation in outreach.content


def test_readiness_score_improves_with_pipeline():
    tracker = ReadinessTracker(candidate=candidate())
    before = tracker.readiness_score()
    tracker.track("gov-001", ApplicationStage.INTERVIEWING)
    assert tracker.readiness_score() > before
    assert tracker.stage_counts()["interviewing"] == 1


def test_readiness_snapshot_shape():
    person = candidate()
    tracker = ReadinessTracker(candidate=person)
    fits = rank(person, discovery.discover(), limit=5)
    snapshot = tracker.snapshot(fits)
    assert 0 <= snapshot["readiness_score"] <= 100
    assert set(snapshot["components"]) == {"credentials", "skills", "pipeline"}
    assert snapshot["next_actions"]


def test_candidate_from_cv_text():
    parsed = profiles.candidate_from_cv_text(
        "Jane Doe",
        "Chief Risk Officer\n18 years across governance and risk management.\n"
        "- Led ESG reporting uplift\n- Completed the AICD Company Directors Course\n",
    )
    assert parsed.years_experience == 18
    assert "governance" in parsed.skills
    assert "risk management" in parsed.skills
    assert "AICD Company Directors Course" in parsed.credentials
    assert parsed.achievements
