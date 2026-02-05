from dataclasses import dataclass, field

@dataclass
class Step:
    """Class for defining what a Step in the job processes lookes like for my runner"""
    name : str
    run : str
    cwd : str | None = None 
@dataclass
class Jobs:
    """ Class for defining what a CI job is for my runner"""
    name : str
    dependency : list[str] = []
    steps : list[Step]
    inputs : list[str] = field(default_factory=list) #For this python creates a new list every time when defining a new job because the input for each job has to be different instead of being stored in one list 
    env : dict[str,str] = field(default_factory=dict)