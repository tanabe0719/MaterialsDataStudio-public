"""Create public synthetic H5XP for explicit native reopen verification."""
from pathlib import Path
from materials_data_studio.tools.command_engine import CommandEngine
from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_waves import empty_document
from materials_data_studio.tools.graph_h5xp import save_h5xp

s = DocumentServices(empty_document())
e = CommandEngine(s)
result = e.execute('Make/D/N=5 a,b,err;a=p+1;b=2*p+1;err=.2;Display/N=MDSNative a;ErrorBars/L=2/Y=3/RGB=(0,0,65535) a XY,wave=(err,err),wave=(err,err);Cursor/P A,a,2;Tag/N=tag0/L=2/X=5/Y=5 a,2,"value=\\\\OY";Display/N=MDSViolin;AppendViolinPlot a,b;ModifyViolinPlot bandwidth=1,showMean=1,showMedian=1;Edit/N=MDSTable a,b;ModifyTable digits(a)=7,width(a)=90;Make/D/N=(4,4) im;im=p+4*q;Display/N=MDSImage;AppendImage im;ColorScale image=im,width=12,heightPct=70,nticks=3,minor=1,fsize=10')
assert result.success, result.message
if __name__ == "__main__":
    import sys
    save_h5xp(Path(sys.argv[1]), s.document)
