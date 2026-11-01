//===-- XtensaTargetStreamer.cpp - Xtensa Target Streamer Methods ---------===//
//
// Part of the LLVM Project, under the Apache License v2.0 with LLVM Exceptions.
// See https://llvm.org/LICENSE.txt for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
//
//===----------------------------------------------------------------------===//
//
// This file provides Xtensa specific target streamer methods.
//
//===----------------------------------------------------------------------===//

#include "XtensaTargetStreamer.h"
#include "MCTargetDesc/XtensaMCAsmInfo.h"
#include "XtensaInstPrinter.h"
#include "llvm/BinaryFormat/ELF.h"
#include "llvm/MC/MCAssembler.h"
#include "llvm/MC/MCContext.h"
#include "llvm/MC/MCObjectFileInfo.h"
#include "llvm/MC/MCSectionELF.h"
#include "llvm/Support/Casting.h"
#include "llvm/Support/CommandLine.h"
#include "llvm/Support/FormattedStream.h"

using namespace llvm;

// GNU as --text-section-literals for compiler-generated pools: keep each
// function's literal pool (.LCPI*, emitted ahead of the function, where L32R
// reaches it) in the function's own code section instead of `.literal.*`.
// GNU ld's Xtensa placement lays out text sections that have a `.literal.*`
// companion apart from those without one, so mixing both splits one object's
// code across the image, out of CALL8 range of each other. Literals the
// assembler creates mid-code (MOVI expansion) keep their separate section.
static cl::opt<bool> XtensaTextSectionLiterals(
    "xtensa-text-section-literals", cl::Hidden, cl::init(false),
    cl::desc("Place compiler literal pools in the text section they serve"));

static bool inlineLiteral(const MCSymbol *LblSym) {
  return XtensaTextSectionLiterals && LblSym->getName().starts_with(".LCPI");
}

static std::string getLiteralSectionName(StringRef CSectionName) {
  std::size_t Pos = CSectionName.find(".text");
  std::string SectionName;
  if (Pos != std::string::npos) {
    SectionName = CSectionName.substr(0, Pos);

    if (Pos > 0)
      SectionName += ".text";

    CSectionName = CSectionName.drop_front(Pos);
    CSectionName.consume_front(".text");

    SectionName += ".literal";
    SectionName += CSectionName;
  } else {
    SectionName = CSectionName;
    SectionName += ".literal";
  }
  return SectionName;
}

XtensaTargetStreamer::XtensaTargetStreamer(MCStreamer &S)
    : MCTargetStreamer(S) {}

XtensaTargetAsmStreamer::XtensaTargetAsmStreamer(MCStreamer &S,
                                                 formatted_raw_ostream &OS)
    : XtensaTargetStreamer(S), OS(OS) {}

void XtensaTargetAsmStreamer::emitLiteral(MCSymbol *LblSym, const MCExpr *Value,
                                          bool SwitchLiteralSection, SMLoc L) {
  SmallString<60> Str;
  raw_svector_ostream LiteralStr(Str);

  LiteralStr << "\t.literal " << LblSym->getName() << ", ";

  if (auto CE = dyn_cast<MCConstantExpr>(Value)) {
    LiteralStr << CE->getValue() << "\n";
  } else if (auto SRE = dyn_cast<MCSymbolRefExpr>(Value)) {
    const MCSymbol &Sym = SRE->getSymbol();
    Xtensa::Specifier Spec =
        static_cast<Xtensa::Specifier>(SRE->getSpecifier());
    LiteralStr << Sym.getName();
    if (Spec == Xtensa::S_TPOFF)
      LiteralStr << "@TPOFF";
    LiteralStr << '\n';
  } else {
    llvm_unreachable("unexpected constant pool entry type");
  }

  OS << LiteralStr.str();
}

void XtensaTargetAsmStreamer::emitLiteralPosition() {
  OS << "\t.literal_position\n";
}

void XtensaTargetAsmStreamer::startLiteralSection(MCSection *BaseSection) {
  emitLiteralPosition();
}

XtensaTargetELFStreamer::XtensaTargetELFStreamer(MCStreamer &S)
    : XtensaTargetStreamer(S) {}

void XtensaTargetELFStreamer::emitLiteral(MCSymbol *LblSym, const MCExpr *Value,
                                          bool SwitchLiteralSection, SMLoc L) {
  MCStreamer &OutStreamer = getStreamer();
  if (inlineLiteral(LblSym)) {
    OutStreamer.emitValueToAlignment(Align(4));
    OutStreamer.emitLabel(LblSym, L);
    OutStreamer.emitValue(Value, 4, L);
    return;
  }
  if (SwitchLiteralSection) {
    MCContext &Context = OutStreamer.getContext();
    auto *CS = static_cast<MCSectionELF *>(OutStreamer.getCurrentSectionOnly());
    std::string SectionName = getLiteralSectionName(CS->getName());

    MCSection *ConstSection = Context.getELFSection(
        SectionName, ELF::SHT_PROGBITS, ELF::SHF_EXECINSTR | ELF::SHF_ALLOC);

    OutStreamer.pushSection();
    OutStreamer.switchSection(ConstSection);
  }

  OutStreamer.emitLabel(LblSym, L);
  OutStreamer.emitValue(Value, 4, L);

  if (SwitchLiteralSection) {
    OutStreamer.popSection();
  }
}

void XtensaTargetELFStreamer::startLiteralSection(MCSection *BaseSection) {
  if (XtensaTextSectionLiterals) {
    getStreamer().switchSection(BaseSection);
    return;
  }
  MCContext &Context = getStreamer().getContext();

  std::string SectionName = getLiteralSectionName(BaseSection->getName());

  MCSection *ConstSection = Context.getELFSection(
      SectionName, ELF::SHT_PROGBITS, ELF::SHF_EXECINSTR | ELF::SHF_ALLOC);

  ConstSection->setAlignment(Align(4));
  MCStreamer &OutStreamer = getStreamer();
  OutStreamer.switchSection(ConstSection);
}

MCELFStreamer &XtensaTargetELFStreamer::getStreamer() {
  return static_cast<MCELFStreamer &>(Streamer);
}
