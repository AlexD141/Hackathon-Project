class Node: 
    def __init__(self, verb : str, dobj : str, nextNode : Node | None, time : float, storage : str):
        self.verb = verb
        self.dobj = dobj
        self.nextNode = nextNode
        self.time = time
        self.storage = storage

    def __str__(self):
        return f"verb: {self.verb}, dobj: {self.dobj}, next step: {self.nextNode.verb + " " + self.nextNode.dobj}, time to cook: {self.time}, utensil: {self.storage}"
#node would be initialized with name = dobj
#next would point to a node