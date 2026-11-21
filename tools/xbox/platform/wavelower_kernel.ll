target datalayout = "e-m:e-p:32:32-i1:32-i8:8-i16:16-i32:32-i64:64-f16:16-f32:32-f64:64-n8:16:32:64"
target triple = "dxil-ms-xb2"

%dx.types.Handle = type { i8* }
%dx.types.CBufRet.i32 = type { i32, i32, i32, i32 }
%dx.types.ResRet.i32 = type { i32, i32, i32, i32, i32 }
%dx.types.ResRet.f32 = type { float, float, float, float, i32 }
%dx.types.ResRet.f16 = type { half, half, half, half, i32 }
%struct.RWByteAddressBuffer = type { i32 }
%BoxParams = type { %struct.BoxRehydrationParams }
%struct.BoxRehydrationParams = type { i32, i32, i32, i32 }


define void @main() {
  %1 = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 1, i32 1, i32 1, i1 false)
  %2 = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 1, i32 0, i32 0, i1 false)
  %3 = call i32 @dx.op.groupId.i32(i32 94, i32 0)
  %4 = call i32 @dx.op.flattenedThreadIdInGroup.i32(i32 96)
  %5 = shl i32 %3, 6
  %6 = add i32 %5, %4
  %7 = shl i32 %6, 4
  %8 = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %2, i32 %7, i32 undef, i8 1, i32 4)
  %9 = extractvalue %dx.types.ResRet.i32 %8, 0
  %10 = call i32 @xbox.LaneSwizzle.i32(i32 53, i32 %9, i32 30)
  %11 = call i32 @xbox.LaneSwizzle.i32(i32 53, i32 %9, i32 60)
  %12 = call i32 @xbox.LaneSwizzle.i32(i32 53, i32 %9, i32 120)
  %13 = call i32 @xbox.ReadLane.i32(i32 51, i32 %9, i32 31)
  %14 = and i32 %9, 1
  %15 = call { i32, i32 } @xbox.Ballot64(i32 25, i32 %14)
  %16 = extractvalue { i32, i32 } %15, 0
  %17 = extractvalue { i32, i32 } %15, 1
  %18 = call i32 @xbox.MBCNT64(i32 107, { i32, i32 } %15)
  %19 = call i32 @xbox.S_BCNT1_U64(i32 130, { i32, i32 } %15)
  %20 = call i32 @xbox.V_CNDMASK_B32(i32 170, { i32, i32 } { i32 -1431655766, i32 -1431655766 }, { i32, i32 } { i32 0, i32 1 })
  %21 = call i32 @xbox.V_CNDMASK_B32(i32 170, { i32, i32 } %15, { i32, i32 } { i32 5, i32 9 })
  %22 = call i32 @xbox.MakeUniform.i32(i32 50, i32 %9)
  %23 = call i32 @xbox.GetLaneID(i32 64)
  %24 = mul i32 %6, 3
  %25 = shl i32 %24, 4
  %26 = add i32 %25, 16
  %27 = add i32 %25, 32
  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %1, i32 %25, i32 undef, i32 %10, i32 %11, i32 %12, i32 %13, i8 15, i32 4)
  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %1, i32 %26, i32 undef, i32 %16, i32 %17, i32 %18, i32 %19, i8 15, i32 4)
  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %1, i32 %27, i32 undef, i32 %20, i32 %21, i32 %22, i32 %23, i8 15, i32 4)
  ret void
}

declare i32 @xbox.LaneSwizzle.i32(i32, i32, i32) #1
declare i32 @xbox.ReadLane.i32(i32, i32, i32) #1
declare { i32, i32 } @xbox.Ballot64(i32, i32) #1
declare i32 @xbox.MBCNT64(i32, { i32, i32 }) #0
declare i32 @xbox.S_BCNT1_U64(i32, { i32, i32 }) #0
declare i32 @xbox.V_CNDMASK_B32(i32, { i32, i32 }, { i32, i32 }) #0
declare i32 @xbox.MakeUniform.i32(i32, i32) #1
declare i32 @xbox.GetLaneID(i32) #0
declare i32 @dx.op.groupId.i32(i32, i32) #0
declare i32 @dx.op.flattenedThreadIdInGroup.i32(i32) #0
declare %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32, %dx.types.Handle, i32, i32, i8, i32) #2
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
!12 = !{i32 0, i64 524304, i32 4, !13}
!13 = !{i32 64, i32 1, i32 1}
