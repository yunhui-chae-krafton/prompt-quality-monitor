#!/usr/bin/env python3
"""Prompt Quality Monitor — CLI.

    pqm.py analyze                 결정론적 지표만, 전량. 무료.
    pqm.py grade --limit 300       LLM 루브릭 채점 (표본). 캐시됨.
    pqm.py dashboard               HTML 대시보드 생성
    pqm.py run --limit 300         위 셋을 순서대로

Korean lemmatization is much better with kiwipiepy installed; the tool falls
back to a regex approximation without it.
"""

import argparse
import json
import os
import sys

from pqm import analyze, extract, llm_grade, report

# Output lives in the user's home, not next to the code. When this ships as a
# plugin the code sits in a managed install directory that is replaced on every
# update, which would take the (paid-for) grade cache with it.
DEFAULT_OUT = os.environ.get(
    "PQM_OUT", os.path.expanduser("~/.prompt-quality-monitor")
)


def _load(args):
    """Extract, attach any cached LLM grades, then analyze.

    Order matters: `build` computes the correlations, and the LLM-graded ones
    are silently dropped if the grades are not on the records yet.
    """
    sessions, records = extract.load_all(
        root=args.root, projects=args.project, since=args.since
    )
    if not records:
        sys.exit("입력한 프롬프트를 찾지 못했습니다. --root / --project 를 확인하세요.")

    llm_grade.attach(records,
                     llm_grade.load_cache(os.path.join(args.out, "llm-cache.json")))
    return analyze.build(sessions, records)


def _persist(result, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    slim = {
        "sessions": result["sessions"],
        "kind_counts": result["kind_counts"],
        "correlations": result["correlations"],
        "vague_top": result["vague_top"],
        "monthly": result["monthly"],
        "weekly": result["weekly"],
        "prompts": [
            {k: v for k, v in r.items() if k != "features"} | {
                "features": {k: v for k, v in r["features"].items()
                             if k != "vague_terms"},
                "vague_terms": r["features"]["vague_terms"],
            }
            for r in result["prompts"]
        ],
    }
    path = os.path.join(out_dir, "analysis.json")
    with open(path, "w") as handle:
        json.dump(slim, handle, ensure_ascii=False)
    return path


def cmd_analyze(args):
    result = _load(args)
    path = _persist(result, args.out)

    md = report.render(result)
    md_path = os.path.join(args.out, "report.md")
    with open(md_path, "w") as handle:
        handle.write(md)

    print(md if args.print_report else f"분석 완료 → {path}\n리포트 → {md_path}")
    return result


def cmd_grade(args):
    result = _load(args)
    os.makedirs(args.out, exist_ok=True)
    cache_path = os.path.join(args.out, "llm-cache.json")

    if args.ingest is not None:
        path = args.ingest or os.path.join(args.out, "grade-response.json")
        info = llm_grade.ingest(path, cache_path)
        print(f"채점 반영: {info['ingested']}개 (건너뜀 {info['skipped']}, "
              f"누적 {info['graded']})")
        return _refresh(args)

    if args.backend == "agent":
        return _emit_request(args, result, cache_path)

    try:
        info = llm_grade.grade(
            result["prompts"], cache_path, limit=args.limit,
            batch_size=args.batch, backend=args.backend, model=args.model,
            command=args.command, progress=lambda m: print(m, flush=True),
        )
    except ValueError as exc:
        sys.exit(str(exc))
    cost = (f", 이번 비용 ${info['cost_usd']:.2f}"
            if args.backend == "claude" else "")
    print(f"채점 완료: 신규 {info['new']}개, 누적 {info['graded']}개{cost}")
    _write_outputs(result, args.out)
    return result


def _emit_request(args, result, cache_path):
    """Hand the grading job to whatever agent is running this."""
    cache = llm_grade.load_cache(cache_path)
    todo = llm_grade.pending(result["prompts"], cache, args.limit)
    if not todo:
        print(f"채점할 새 프롬프트가 없습니다 (누적 {len(cache)}개).")
        return result

    request_path = os.path.join(args.out, "grade-request.json")
    llm_grade.write_request(todo, request_path, batch_size=args.batch)
    print(
        f"채점 요청 {len(todo)}개를 준비했습니다.\n"
        f"  요청: {request_path}\n"
        f"  이 파일의 rubric 을 items 에 적용해 결과를 아래에 쓰십시오.\n"
        f"  응답: {os.path.join(args.out, 'grade-response.json')}\n"
        f"       [{{\"id\": \"<items의 id 그대로>\", \"coherence\": 0-10, "
        f"\"complexity\": 0-10, \"clarity\": 0-10, \"issue\": \"\", "
        f"\"rewrite\": \"\"}}, ...]\n"
        f"  반영: pqm.py grade --ingest"
    )
    return result


def _refresh(args):
    """Re-run the analysis so reports pick up newly ingested grades."""
    result = _load(args)
    _write_outputs(result, args.out)
    return result


def _write_outputs(result, out_dir):
    _persist(result, out_dir)
    with open(os.path.join(out_dir, "report.md"), "w") as handle:
        handle.write(report.render(result))


def cmd_dashboard(args):
    from pqm import dashboard
    result = _load(args)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "dashboard.html")
    with open(path, "w") as handle:
        handle.write(dashboard.render(result))
    print(f"대시보드 → {path}")


def cmd_run(args):
    cmd_grade(args)
    cmd_dashboard(args)


def main():
    # Shared options live on a parent parser so they are accepted on either
    # side of the subcommand; argparse otherwise rejects `analyze --out ...`.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=extract.DEFAULT_ROOT,
                        help="Claude 프로젝트 디렉토리 (기본: ~/.claude/projects)")
    common.add_argument("--project", action="append",
                        help="프로젝트 디렉토리명 부분 일치 필터 (반복 가능)")
    common.add_argument("--since", help="ISO 날짜. 이후 시작된 세션만")
    common.add_argument("--out", default=DEFAULT_OUT, help="출력 디렉토리")

    parser = argparse.ArgumentParser(
        parents=[common],
        description="Claude Code 세션 로그에서 내 프롬프트를 뽑아 점수화한다.")
    subs = parser.add_subparsers(dest="command", required=True)

    sub = subs.add_parser("analyze", parents=[common],
                          help="결정론적 지표 (무료, 전량)")
    sub.add_argument("--print-report", action="store_true",
                     help="리포트를 표준출력으로")
    sub.set_defaults(func=cmd_analyze)

    for name, func, help_text in (("grade", cmd_grade, "LLM 루브릭 채점 (표본)"),
                                  ("run", cmd_run, "채점 + 대시보드")):
        sub = subs.add_parser(name, parents=[common], help=help_text)
        sub.add_argument("--limit", type=int, default=300, help="채점 표본 크기")
        sub.add_argument("--batch", type=int, default=12, help="한 배치당 프롬프트 수")
        sub.add_argument(
            "--backend", choices=llm_grade.BACKENDS, default="agent",
            help="agent: 이 스킬을 실행 중인 에이전트가 채점 (기본, 추가 비용 없음). "
                 "claude: claude CLI 로 무인 채점. "
                 "command: --command 로 지정한 임의의 명령에 위임.")
        sub.add_argument("--command",
                         help="backend=command 일 때 실행할 명령. "
                              "stdin 으로 프롬프트를 받고 stdout 으로 JSON 배열을 낸다. "
                              "예: 'codex exec -' / 'opencode run -' / 'ollama run qwen3'")
        sub.add_argument("--ingest", nargs="?", const="", default=None,
                         metavar="PATH",
                         help="에이전트가 쓴 응답 파일을 캐시에 반영 "
                              "(생략 시 <out>/grade-response.json)")
        sub.add_argument("--model", default="claude-sonnet-5",
                         help="backend=claude 일 때 사용할 모델")
        sub.set_defaults(func=func)

    sub = subs.add_parser("dashboard", parents=[common],
                          help="HTML 대시보드 생성")
    sub.set_defaults(func=cmd_dashboard)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
