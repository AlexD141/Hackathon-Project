PASSIVE = {"simmer", "boil"}
from Node import Node

#pots were stored as pot#i or skillet#i
#turn these into nouns
#pot#1 -> pot
def vessel(name : str):
    if not name or name == "temp":
        return None
    return name.split("#")[0]

def joinList(items : dict):
    items = list(dict.fromkeys(items))
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]

#turn a global time into readable time
def fmtTime(sec : float):
    if not sec:
        return ""
    for unit, size in (("hour", 3600), ("minute", 60), ("second", 1)):
        if sec >= size:
            n = sec / size
            n = int(n) if n == int(n) else round(n, 1)
            return f"{n} {unit}{'s' if n != 1 else ''}"
    return ""


def groupsOf(nodes : list[Node]):
    groups : list[list[Node]] = []
    index : dict[int, int] = {}
    for n in nodes:
        if n.group not in index:
            index[n.group] = len(groups)
            groups.append([])
        groups[index[n.group]].append(n)
    return groups


def sentence(group : list[Node]):
    first = group[0]
    verb = first.state.capitalize()
    objs = joinList(n.obj for n in group)
    loc, src = vessel(first.loc), vessel(first.src)
    parts = []
    if first.washBefore and loc:
        parts.append(f"Wash the {loc}.")
    if first.state == "transfer":
        where = f" from the {src}" if src else ""
        dest = f" to the {loc}" if loc else ""
        parts.append(f"{verb} {objs}{where}{dest}")
    elif first.state in PASSIVE:
        parts.append(f"{verb} {objs}" + (f" in the {loc}" if loc else ""))
    else:
        parts.append(f"{verb} {objs}" + (f" in the {loc}" if loc else ""))
    t = fmtTime(first.duration)
    if t:
        parts[-1] += f" for {t}"
    if first.state in PASSIVE:
        parts[-1] += " (you can work on something else meanwhile)"
    return " ".join(parts) + ("" if parts[-1].endswith(".") else ".")


def render(nodes : list[Node]):
    groups = groupsOf(nodes)
    lines : list[str] = []
    prev = set()
    for k, g in enumerate(groups, 1):
        text = sentence(g)
        independent = prev and not any(p in prev for n in g for p in n.preds)
        if independent and not g[0].washBefore:
            text = "Meanwhile, " + text[0].lower() + text[1:]
        lines.append(f"{k}. {text}")
        prev = set(g)
    return "\n".join(lines)