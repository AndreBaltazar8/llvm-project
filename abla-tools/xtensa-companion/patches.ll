target triple = "xtensa-esp-unknown-elf"
declare void @external()
define void @target() { ret void }
define void @direct() {
  call void @target()
  ret void
}
define i32 @mask(i32 %x) {
  %masked = and i32 %x, 255
  ret i32 %masked
}
define i32 @bit(i32 %x, i32 %y) {
  %bit = and i32 %x, 4
  %clear = icmp eq i32 %bit, 0
  br i1 %clear, label %a, label %b
a:
  call void @external()
  ret i32 %y
b:
  ret i32 2
}
define i32 @zero(i32 %x) {
  %clear = icmp eq i32 %x, 0
  br i1 %clear, label %a, label %b
a:
  call void @external()
  ret i32 1
b:
  ret i32 2
}
