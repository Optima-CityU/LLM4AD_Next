// LLM4AD NSFC proposal presentation layer.
//
// Layout parameters are adapted from Readon/NSFC-application-template-typst
// at commit 39e6baf84a3db6691c2e3c95134a7dbb97f1de6b:
// https://github.com/Readon/NSFC-application-template-typst
// The upstream 2023 outline was intentionally not copied. LLM4AD supplies the
// current outline in proposal.typ so content structure can evolve independently
// from this presentation layer. The remote cuti package was also removed so the
// template compiles in the browser without package-network access.

#let nsfc-blue = rgb("#0070c0")
#let nsfc-body-font = ("Libertinus Serif", "Noto Serif CJK SC")
#let nsfc-heading-font = ("Noto Serif CJK SC", "Libertinus Serif")

#let nsfc-fixed-heading(body) = block(
  above: 18.2pt,
  below: 7pt,
  breakable: false,
)[
  #set text(size: 14pt, font: nsfc-heading-font, weight: "bold", fill: nsfc-blue)
  #set par(first-line-indent: 21pt, justify: true)
  #body
]

#let nsfc-proposal(title: "报告正文", body) = {
  set document(title: title)
  set page(
    paper: "a4",
    margin: (top: 2.78cm, bottom: 2.5cm, left: 3cm, right: 3cm),
    numbering: none,
  )
  set text(size: 11.8pt, font: nsfc-body-font, lang: "zh", region: "cn")
  set par(leading: 14.5pt, first-line-indent: 24pt, justify: true)
  show par: set block(spacing: 1em, above: 14.2pt)
  set heading(numbering: none)

  show heading.where(level: 1): it => nsfc-fixed-heading(it.body)
  show heading.where(level: 2): it => block(
    above: 12pt,
    below: 4pt,
    breakable: false,
  )[
    #set text(size: 11.8pt, font: nsfc-heading-font, weight: "bold")
    #set par(first-line-indent: 24pt, justify: true)
    #it.body
  ]

  align(center)[
    #text(size: 15.5pt, font: nsfc-heading-font, weight: "bold")[报告正文]
  ]
  v(16.5pt)
  set text(size: 14pt, font: nsfc-heading-font)
  set par(leading: 13pt, first-line-indent: 28pt, justify: true)
  [参照以下提纲撰写，要求内容翔实、清晰，层次分明，标题突出。申请书正文原则上不超过30页，鼓励简洁表达。]
  text(weight: "bold", fill: nsfc-blue)[请勿删除或改动下述提纲标题及括号中的文字。]

  set text(size: 11.8pt, font: nsfc-body-font, lang: "zh", region: "cn")
  set par(leading: 14.5pt, first-line-indent: 24pt, justify: true)
  body
}

// MIT License
//
// Copyright (c) 2024 Readon
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in all
// copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
// SOFTWARE.
