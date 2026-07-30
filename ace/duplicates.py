"""Near-duplicate ticket detection over free-text Descriptions (pure, no DB).

Same underlying issue often gets raised more than once as separate ASPL
tickets, worded differently. We catch those by comparing the *word sets* of
their Descriptions with Jaccard similarity (|A∩B| / |A∪B|), which is robust to
re-ordering and minor rewording. Tickets are then clustered with union-find so a
group of 3+ near-identical tickets comes back as one cluster.

Everything runs in memory over the already-fetched rows. To stay fast we build
an inverted index (token -> tickets) and only compare pairs that share a
reasonably distinctive word, instead of all N^2 pairs.
"""
import re
from collections import defaultdict

# Common words that carry no signal for "is this the same issue".
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
    "was", "were", "be", "been", "this", "that", "it", "its", "as", "at", "by",
    "with", "from", "not", "no", "we", "i", "you", "he", "she", "they",
    "please", "kindly", "need", "needs", "want", "should", "would", "when",
    "which", "while", "also", "but", "if", "then", "so", "do", "does", "done",
    "has", "have", "had", "will", "shall", "can", "could", "there", "their",
    "our", "your", "us", "me", "my", "am", "pm", "get", "got", "make", "made",
    "hi", "hello", "dear", "team", "issue", "raised",
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _stem(t: str) -> str:
    """Very light suffix stripping so plural/verb forms collapse together
    (patients->patient, reports->report, showing->show, updated->updat). Crude
    on purpose — just enough to match the same word written a different way."""
    for suf in ("ing", "ed", "es", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            return t[: -len(suf)]
    return t


def tokenize(text) -> set:
    """Lowercase, lightly-stemmed word-set with stopwords and 1-char tokens
    removed."""
    if not text:
        return set()
    return {_stem(t) for t in _TOKEN_RE.findall(str(text).lower())
            if len(t) >= 2 and t not in _STOPWORDS}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]  # path compression
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _preview(r: dict, n: int) -> dict:
    return {
        "aspl": r.get("ASPL#"),
        "client": r.get("Client Name"),
        "employee": r.get("Employee Name"),
        "task_type": r.get("Task Name"),
        "status": r.get("ASPL Status"),
        "ageing_days": r.get("Agening"),
        "description": str(r.get("Description") or "")[:n],
    }


def find_similar(rows, min_similarity=0.5, same_client_only=False,
                 min_words=4, max_clusters=40, preview_len=160):
    """Cluster tickets whose Descriptions are >= `min_similarity` alike.

    Returns (summary_dict). Only rows with at least `min_words` distinctive
    words are considered (blank/one-line descriptions can't be judged similar).
    """
    items = []  # (row, token_set)
    for r in rows:
        ts = tokenize(r.get("Description"))
        if len(ts) >= min_words:
            items.append((r, ts))
    n = len(items)

    # Inverted index. Skip ultra-common tokens (present in >40% of items): they
    # aren't distinctive and would create huge candidate lists.
    df = defaultdict(int)
    for _, ts in items:
        for t in ts:
            df[t] += 1
    common_cutoff = max(5, int(0.40 * n)) if n else 0
    index = defaultdict(list)
    for i, (_, ts) in enumerate(items):
        for t in ts:
            if df[t] <= common_cutoff:
                index[t].append(i)

    uf = _UnionFind(n)
    pair_sim = {}
    checked = set()
    for idxs in index.values():
        if len(idxs) < 2:
            continue
        for ai in range(len(idxs)):
            for bi in range(ai + 1, len(idxs)):
                a, b = idxs[ai], idxs[bi]
                key = (a, b)
                if key in checked:
                    continue
                checked.add(key)
                ra, ta = items[a]
                rb, tb = items[b]
                if same_client_only and (
                        str(ra.get("Client Name") or "").strip()
                        != str(rb.get("Client Name") or "").strip()):
                    continue
                sim = _jaccard(ta, tb)
                if sim >= min_similarity:
                    uf.union(a, b)
                    pair_sim[key] = sim

    # Gather clusters (root -> member indices), keep only real duplicates (2+).
    groups = defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(i)
    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        # average similarity across the pairs we actually scored in this cluster
        sims = [pair_sim[(a, b)]
                for ai, a in enumerate(members)
                for b in members[ai + 1:]
                if (a, b) in pair_sim]
        avg = round(sum(sims) / len(sims), 2) if sims else min_similarity
        clusters.append({
            "size": len(members),
            "avg_similarity": avg,
            "tickets": [_preview(items[i][0], preview_len) for i in members],
        })

    clusters.sort(key=lambda c: (c["size"], c["avg_similarity"]), reverse=True)
    total_dupe_tickets = sum(c["size"] for c in clusters)
    return {
        "params": {
            "min_similarity": min_similarity,
            "same_client_only": same_client_only,
            "min_words": min_words,
        },
        "clusters_found": len(clusters),
        "tickets_in_duplicate_clusters": total_dupe_tickets,
        "considered_tickets": n,
        "note": ("Similarity is word-set overlap (Jaccard) of the Description. "
                 "1.0 = identical wording; ~0.6+ usually means the same issue "
                 "re-raised. Lower min_similarity to catch looser matches."),
        "clusters": clusters[:max_clusters],
    }
