"""The Visualization Studio's toolkit: graphs of runs and experiments, changed by the researcher
or by their own CREAO agent through checked operations. The agent decides what to show; this
package does every calculation and builds every figure. Design: Documents/visualization-design.md.

    spec.py        the graph spec, a JSON document that fully describes one graph
    sources.py     the datasets a viewer may graph: watched runs and experiments
    transforms.py  the numerics: x axes, ranges, alignment, statistics, display downsampling
    figure.py      the Plotly figure for a spec
    ops.py         the operations that change a spec, each checked before it applies
    agent.py       what the researcher's agent is told and sent about a graph
    store.py       saved graphs and their history, in the researcher's private database
"""
