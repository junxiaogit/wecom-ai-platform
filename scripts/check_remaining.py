"""检查D0A55F87剩余3个单的相似度"""
import sys, re
sys.stdout.reconfigure(encoding='utf-8')

def _extract_bigrams(text):
    text = text.lower().strip()
    text = re.sub(r'[，。、！？：；""\'\'（）\(\)\[\]\s]+', '', text)
    if len(text) < 2:
        return set()
    return {text[i:i+2] for i in range(len(text) - 1)}

pairs = [
    ("TikTok无网络代理设置异常", "云机App无网络但浏览器有网"),
    ("TikTok无网络代理设置异常", "云机部分应用无网络"),
    ("云机App无网络但浏览器有网", "云机部分应用无网络"),
]

for a, b in pairs:
    bg_a, bg_b = _extract_bigrams(a), _extract_bigrams(b)
    overlap = len(bg_a & bg_b)
    union = len(bg_a | bg_b)
    min_len = min(len(bg_a), len(bg_b))
    j = overlap / union if union > 0 else 0
    c = overlap / min_len if min_len > 0 else 0
    s = max(j, c)
    print(f"  {a}  vs  {b}")
    print(f"  sim={s:.2f} (jaccard={j:.2f}, containment={c:.2f})")
    print()
