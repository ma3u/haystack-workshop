"""
Graph memory as a CLI. Claude Code calls it through the Bash tool.

    venv/bin/python claude_memory/cli.py remember "User" "works at" "deepset"
    venv/bin/python claude_memory/cli.py recall            # everything
    venv/bin/python claude_memory/cli.py recall deepset --hops 1
    venv/bin/python claude_memory/cli.py forget "User" LIVES_IN "Berlin"
    venv/bin/python claude_memory/cli.py recall --history  # include forgotten facts
"""
import argparse

from _common import open_memory


def main() -> None:
    p = argparse.ArgumentParser(description="Long-term graph memory (Neo4j). Facts are subject -relation-> object.")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("remember", help="store one fact; use 'User' for the person you talk to")
    r.add_argument("subject"); r.add_argument("relation"); r.add_argument("object")
    q = sub.add_parser("recall", help="look up facts; no query = everything")
    q.add_argument("query", nargs="?", default="")
    q.add_argument("--hops", type=int, default=2)
    q.add_argument("--history", action="store_true", help="also show forgotten (retired) facts")
    f = sub.add_parser("forget", help="retire one fact; relation as shown by recall")
    f.add_argument("subject"); f.add_argument("relation"); f.add_argument("object")
    args = p.parse_args()

    memory = open_memory()
    try:
        if args.cmd == "remember":
            print(memory.remember(args.subject, args.relation, args.object))
        elif args.cmd == "recall":
            print(memory.recall(args.query, hops=args.hops, history=args.history))
        else:
            print(memory.forget(args.subject, args.relation, args.object))
    finally:
        memory.close()


if __name__ == "__main__":
    main()
