# Models

## Who wrote this pack

The code, the rules, the scenarios, the tests and the documents of the proof pack v2.0 were written by a
synthetic AI agent, Tomaso Riviera, running on **Claude Opus 5.5** (`claude-opus-5-5`). Commits are authored as
`Tomaso Riviera (synthetic alumnus, via Claude Opus 5.5)` with the trailer
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. No human wrote code in this pack; a human approved
the plan it was built from.

The profile text above the proof-pack section of `README.md` predates this pack and was not written in this
work; it names another advisor model and is left as it was.

## No model at runtime

Nothing in this repository calls a model, an API or any remote service:

- the harness, the generator, the references, the scorer, the scenarios and the tools import the Python
  standard library and each other, nothing else (`tests/test_no_network.py`);
- every decision is taken by ordered rule files (`rules/*.json`, first match wins) and exact arithmetic;
- the same seed gives the same bytes on every run (`tests/test_determinism.py`, `tools/rebuild.py --double`).

## Optional model hook

There is none in v2.0. If one is ever added it must be disabled by default, must not be needed by any test or
scenario, and may only name `claude-opus-5-5` or `claude-fable-5-1`.

## Third-party components

None. Python 3.12, standard library only; `requirements.txt` lists no package.

## The blind run is not the author's

After the tag `v2.0.0-freeze` the blind evaluation is run once by a different hand: another session on the
other model of the fleet (`claude-fable-5-1`, since the code is by `claude-opus-5-5`) or the Rector. The name
of whoever runs it is recorded in `eval/history.json`. See `eval/BLIND_PROTOCOL.md`.

## Same author on both sides

The harness and the independent references (`corpus/reference_*.py`) share no code, but they were written by
the same author from the same plan. An error in the author's understanding of a rule can be on both sides;
the hand-computed expected values of the scenarios and the blind run are the checks against that.
