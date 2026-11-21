target datalayout = "e-m:e-p:32:32-i1:32-i8:8-i16:16-i32:32-i64:64-f16:16-f32:32-f64:64-n8:16:32:64"
target triple = "dxil-ms-xb2"

%dx.types.Handle = type { i8* }
%dx.types.ResRet.i32 = type { i32, i32, i32, i32, i32 }
%struct.RWByteAddressBuffer = type { i32 }

define void @main() {
  %1 = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 1, i32 1, i32 1, i1 false)
  %2 = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 1, i32 0, i32 0, i1 false)
  %3 = call i32 @dx.op.groupId.i32(i32 94, i32 0)
  %4 = call i32 @dx.op.flattenedThreadIdInGroup.i32(i32 96)
  %5 = shl i32 %3, 6
  %6 = add i32 %5, %4
  %7 = call %dx.types.ResRet.i32 @xbox.TypedLoad.i32(i32 138, %dx.types.Handle %2, i32 %6, i32 65)
  %8 = extractvalue %dx.types.ResRet.i32 %7, 0
  %9 = mul i32 %6, 5
  %10 = add i32 %9, 3
  %11 = call %dx.types.ResRet.i32 @xbox.TypedLoad.i32(i32 138, %dx.types.Handle %2, i32 %10, i32 65)
  %12 = extractvalue %dx.types.ResRet.i32 %11, 0
  %13 = shl i32 %6, 3
  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %1, i32 %13, i32 undef, i32 %8, i32 %12, i32 undef, i32 undef, i8 3, i32 4)
  ret void
}

declare %dx.types.ResRet.i32 @xbox.TypedLoad.i32(i32, %dx.types.Handle, i32, i32) #2
declare i32 @dx.op.groupId.i32(i32, i32) #0
declare i32 @dx.op.flattenedThreadIdInGroup.i32(i32) #0
declare void @dx.op.rawBufferStore.i32(i32, %dx.types.Handle, i32, i32, i32, i32, i32, i32, i8, i32) #1
declare %dx.types.Handle @dx.op.createHandle(i32, i8, i32, i32, i1) #2
attributes #0 = { nounwind readnone }
attributes #1 = { nounwind }
attributes #2 = { nounwind readonly }
!xbox.compiler.options = !{!0}
!llvm.ident = !{!1}
!dx.version = !{!2}
!dx.valver = !{!3}
!dx.shaderModel = !{!4}
!dx.resources = !{!5}
!dx.entryPoints = !{!11}
!0 = !{!"__XBOX_INLINE_SOURCE", !"1"}
!1 = !{!"dxcxbox 2504.2509.11601.10011"}
!2 = !{i32 1, i32 5}
!3 = !{i32 1, i32 8}
!4 = !{!"cs", i32 6, i32 5}
!5 = !{null, !6, null, null}
!6 = !{!7, !8}
!7 = !{i32 0, %struct.RWByteAddressBuffer* undef, !"", i32 0, i32 0, i32 1, i32 11, i1 false, i1 false, i1 false, null}
!8 = !{i32 1, %struct.RWByteAddressBuffer* undef, !"", i32 0, i32 1, i32 1, i32 11, i1 false, i1 false, i1 false, null}
!11 = !{void ()* @main, !"main", null, !5, !12}
!12 = !{i32 0, i64 16, i32 4, !13}
!13 = !{i32 64, i32 1, i32 1}
