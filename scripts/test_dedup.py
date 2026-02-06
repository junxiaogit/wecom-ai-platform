"""测试中文去重 bigram 算法"""
import sys, re
sys.stdout.reconfigure(encoding='utf-8')

def _extract_bigrams(text):
    text = text.lower().strip()
    text = re.sub(r'[，。、！？：；""\'\'（）\(\)\[\]\s]+', '', text)
    if len(text) < 2:
        return set()
    return {text[i:i+2] for i in range(len(text) - 1)}

def compare(label, a_text, b_text):
    a = _extract_bigrams(a_text)
    b = _extract_bigrams(b_text)
    overlap = len(a & b)
    union = len(a | b)
    sim = overlap / union if union > 0 else 0
    print(f"[{label}]")
    print(f"  A: {a_text}")
    print(f"  B: {b_text}")
    print(f"  Bigrams: A={len(a)}, B={len(b)}, Overlap={overlap}, Union={union}")
    print(f"  Similarity: {sim:.2f} (threshold=0.7) -> {'DEDUP' if sim >= 0.7 else 'PASS'}")
    print()

# Case 1: 今天的重复（应该去重）
compare("Case1: same issue different wording",
    "Venmo无法使用、安卓13谷歌钱包异常、云机无法开机",
    "Venmo无法使用、安卓13谷歌钱包异常、云机不开机")

# Case 2: 同一问题不同表述（应该去重）
compare("Case2: same issue rephrased",
    "云机App无网络但浏览器有网",
    "TikTok无网络代理设置异常")

# Case 3: 完全不同的问题（不应去重）
compare("Case3: different issues",
    "TikTok无网络代理设置异常",
    "批量启动云机后部分无法启动")

# Case 4: 高度相似（应该去重）
compare("Case4: nearly identical",
    "云机启动失败",
    "云机启动不了")

# OLD method comparison
print("=" * 60)
print("OLD .split() method (BUG):")
old_a = set("云机App无网络但浏览器有网".split())
old_b = set("云机部分应用无网络".split())
old_overlap = len(old_a & old_b)
old_max = max(len(old_a), len(old_b))
old_sim = old_overlap / old_max if old_max > 0 else 0
print(f"  A words: {old_a}")
print(f"  B words: {old_b}")
print(f"  Similarity: {old_sim:.2f} -- always 0 for Chinese!")
