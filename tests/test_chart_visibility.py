import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from backend.bioflow_platform.chart_contracts import DisplayParameters
from backend.bioflow_platform.chart_display import display


def test_title_and_axis_switches_are_independent():
 fig,ax=plt.subplots();ax.set(title='Title',xlabel='X',ylabel='Y')
 try:
  p=DisplayParameters(show_title=True,show_x_label=False,show_y_label=True).model_dump();display(fig,ax,p)
  assert ax.title.get_visible() and not ax.xaxis.label.get_visible() and ax.yaxis.label.get_visible()
  display(fig,ax,DisplayParameters(show_title=False,show_x_label=True,show_y_label=False).model_dump())
  assert not ax.title.get_visible() and ax.xaxis.label.get_visible() and not ax.yaxis.label.get_visible()
 finally:plt.close(fig)
