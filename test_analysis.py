"""测试 KataGo analysis 模式"""
import subprocess, json, time, sys, threading

MODEL = '/Users/jinghuahao/Desktop/小H/go-ai/kata1-b28c512nbt-s12763923712-d5805955894.bin.gz'
CONFIG = '/Users/jinghuahao/Desktop/小H/go-ai/analysis.cfg'

p = subprocess.Popen(
    ['katago', 'analysis', '-model', MODEL, '-config', CONFIG],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
)

def drain():
    for line in p.stderr:
        pass  # silent
t = threading.Thread(target=drain, daemon=True)
t.start()

time.sleep(5)

# 方式1: 用 moves + analyzeTurns
query1 = {
    "id": "q1",
    "moves": [["B","D4"],["W","Q16"]],
    "rules": "chinese",
    "komi": 6.5,
    "boardXSize": 19,
    "boardYSize": 19,
    "analyzeTurns": [2],
    "maxVisits": 300
}

# 方式2: 用 initialStones + initialPlayer, 没有 moves, 没有 analyzeTurns
query2 = {
    "id": "q2",
    "initialStones": [["B","D4"],["W","Q16"]],
    "initialPlayer": "B",
    "rules": "chinese",
    "komi": 6.5,
    "boardXSize": 19,
    "boardYSize": 19,
    "maxVisits": 300
}

for q in [query1, query2]:
    print(f"\n=== Query {q['id']} ===")
    p.stdin.write(json.dumps(q) + "\n")
    p.stdin.flush()
    line = p.stdout.readline()
    if not line:
        print("No response!")
        continue
    resp = json.loads(line)
    print(f"  id={resp.get('id')}  turnNumber={resp.get('turnNumber')}")
    if 'error' in resp:
        print(f"  ERROR: {resp['error']}")
    infos = resp.get("moveInfos", [])
    print(f"  moveInfos count: {len(infos)}")
    for mi in infos[:5]:
        wr = mi['winrate'] * 100
        sl = mi.get('scoreLead', 0)
        print(f"    {mi['move']:>4s}  winrate={wr:.1f}%  score={sl:+.1f}  visits={mi['visits']}")

p.terminate()
print("\nDone!")
