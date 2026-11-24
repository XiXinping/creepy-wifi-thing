"""STEP -> GLB keeping XCAF part names + colours. Run: .venv/bin/python -I blender/step_to_glb.py in.step out.glb"""
import sys
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TDocStd import TDocStd_Document
from OCP.TCollection import TCollection_ExtendedString, TCollection_AsciiString
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.RWGltf import RWGltf_CafWriter
from OCP.collections import IndexedDataMap_TCollection_AsciiString_TCollection_AsciiString as FileInfo
from OCP.Message import Message_ProgressRange
from OCP.IFSelect import IFSelect_RetDone

src, dst = sys.argv[1], sys.argv[2]
doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
reader = STEPCAFControl_Reader()
reader.SetColorMode(True)
reader.SetNameMode(True)
reader.SetLayerMode(True)
assert reader.ReadFile(src) == IFSelect_RetDone, "STEP read failed"
assert reader.Transfer(doc), "STEP transfer failed"

# Meshing the reader's root shape triangulates the same TShapes the XCAF doc references.
# fine tessellation: 0.01 mm linear deflection, 0.1 rad angular, shapes are in mm
BRepMesh_IncrementalMesh(reader.Reader().OneShape(), 0.01, False, 0.1, True)

w = RWGltf_CafWriter(TCollection_AsciiString(dst), True)  # binary glb
w.ChangeCoordinateSystemConverter().SetInputLengthUnit(0.001)  # mm -> m
assert w.Perform(doc, FileInfo(), Message_ProgressRange()), "glTF write failed"
print("wrote", dst)
