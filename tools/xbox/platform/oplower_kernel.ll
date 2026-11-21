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
  %8 = call %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32 139, %dx.types.Handle %2, i32 %7, i32 undef, i8 15, i32 4)
  %9 = extractvalue %dx.types.ResRet.i32 %8, 0
  %10 = extractvalue %dx.types.ResRet.i32 %8, 1
  %11 = extractvalue %dx.types.ResRet.i32 %8, 2
  %12 = bitcast i32 %9 to float
  %13 = bitcast i32 %10 to float
  %14 = bitcast i32 %11 to float
  %15 = call float @xbox.Med3_F32(i32 10, float %12, float %13, float %14)
  %17 = call %dx.types.ResRet.f16 @dx.op.rawBufferLoad.f16(i32 139, %dx.types.Handle %2, i32 %7, i32 undef, i8 15, i32 2)
  %23 = extractvalue %dx.types.ResRet.f16 %17, 0
  %24 = extractvalue %dx.types.ResRet.f16 %17, 1
  %25 = extractvalue %dx.types.ResRet.f16 %17, 2
  %26 = extractvalue %dx.types.ResRet.f16 %17, 3
  %27 = insertvalue { half, half } undef, half %23, 0
  %28 = insertvalue { half, half } %27, half %24, 1
  %29 = insertvalue { half, half } undef, half %25, 0
  %30 = insertvalue { half, half } %29, half %26, 1
  %31 = call { half, half } @xbox.V_PK_MIN_F16(i32 223, { half, half } %28, { half, half } %30)
  %32 = call { half, half } @xbox.V_PK_MAX_F16(i32 224, { half, half } %28, { half, half } %30)
  %33 = extractvalue { half, half } %31, 0
  %34 = extractvalue { half, half } %31, 1
  %35 = extractvalue { half, half } %32, 0
  %36 = extractvalue { half, half } %32, 1
  %50 = or i32 %7, 4
  %51 = or i32 %7, 8
  call void @dx.op.rawBufferStore.f32(i32 140, %dx.types.Handle %1, i32 %7, i32 undef, float %15, float undef, float undef, float undef, i8 1, i32 4)
  call void @dx.op.rawBufferStore.f16(i32 140, %dx.types.Handle %1, i32 %50, i32 undef, half %33, half %34, half undef, half undef, i8 3, i32 2)
  call void @dx.op.rawBufferStore.f16(i32 140, %dx.types.Handle %1, i32 %51, i32 undef, half %35, half %36, half undef, half undef, i8 3, i32 2)
  ret void
}

declare float @xbox.Med3_F32(i32, float, float, float) #0
declare { half, half } @xbox.V_PK_MIN_F16(i32, { half, half }, { half, half }) #0
declare { half, half } @xbox.V_PK_MAX_F16(i32, { half, half }, { half, half }) #0
declare i32 @dx.op.groupId.i32(i32, i32) #0
declare i32 @dx.op.flattenedThreadIdInGroup.i32(i32) #0
declare %dx.types.ResRet.i32 @dx.op.rawBufferLoad.i32(i32, %dx.types.Handle, i32, i32, i8, i32) #2
declare %dx.types.ResRet.f16 @dx.op.rawBufferLoad.f16(i32, %dx.types.Handle, i32, i32, i8, i32) #2
declare void @dx.op.rawBufferStore.f32(i32, %dx.types.Handle, i32, i32, float, float, float, float, i8, i32) #1
declare void @dx.op.rawBufferStore.f16(i32, %dx.types.Handle, i32, i32, half, half, half, half, i8, i32) #1
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
!12 = !{i32 0, i64 48, i32 4, !13}
!13 = !{i32 64, i32 1, i32 1}
