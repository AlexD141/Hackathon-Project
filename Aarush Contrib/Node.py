class Node:
    def __init__(self, obj : str, loc : str, i : int, state : str, duration:float=0, active=True):
        self.obj, self.loc, self.i, self.state = obj, loc, i, state
        self.src : str | None = None              # which container an item came from
        self.group = -1              # nodes made by one verb call are siblings
        self.duration = duration     # seconds to cook
        self.active = active         # does this need active attention?
        self.washBefore : bool = False      # vessel reused: wash first
        self.preds, self.succs = [], []

    def __str__(self):
        return f"{self.obj}:{self.state}:{self.loc}:{self.i}"

    __repr__ = __str__ 