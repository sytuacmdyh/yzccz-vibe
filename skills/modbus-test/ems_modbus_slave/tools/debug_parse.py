from pathlib import Path
import sys
sys.path.insert(0, "tools")
from spot_check_dm_hp_master_v2 import parse_row, split_cells

lines = Path("../造梦者热泵/造梦者热泵主控通信协议V2.0.md").read_text(encoding="utf-8").splitlines()
for n in [99, 100, 101, 581]:
    raw = lines[n-1]
    print("LINE", n, "starts", raw[:20])
    print(" cells0", split_cells(raw)[:3] if split_cells(raw) else None)
    print(" parsed", parse_row(n, raw))
