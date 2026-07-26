// Finds shopper-facing English that never reached the dictionary.
//
// This exists because the thing it replaces did not work. `UNEXTRACTED_SURFACES`
// was a hand-maintained list of components still holding literals, and a
// coverage number computed against it read 100% while the checkout was
// entirely English - the list was an assertion, and assertions rot silently.
// A locale is only offered when this check passes, so the claim "the interface
// speaks Vietnamese" is now verified by walking the syntax tree rather than by
// remembering to update a constant.
import ts from 'typescript'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const SRC = new URL('../src/', import.meta.url).pathname
const ROOT = new URL('..', import.meta.url).pathname

// Attributes whose values a shopper reads or hears. `alt`, `aria-label`,
// `title` and `placeholder` are text; everything else on a DOM element is
// machinery. `label` is here because components take it as a prop and pass it
// straight to `aria-label` - the string is just as visible for being one
// indirection away.
const TEXT_ATTRIBUTES = new Set([
  'alt',
  'aria-label',
  'label',
  'title',
  'placeholder',
])

// Object properties whose values a shopper reads. A string only has to be
// written *somewhere* to be rendered later: the nudges lived in `presence.ts`,
// the assistant's replies in `api.ts` and the welcome line in a module-level
// constant, and all of them reached the screen while a check that read only
// markup reported the interface fully translated. Matching on the property
// name catches the string where it is written instead.
//
// `raw` is here on purpose. `LocalizedText` accepts `{ raw }` for prose the
// assistant service wrote in the shopper's language, so a literal in that
// position is English being carried past the type.
const TEXT_PROPERTIES = new Set([
  'alt',
  'ariaLabel',
  'caption',
  'description',
  'heading',
  'hint',
  'label',
  'message',
  'opener',
  'placeholder',
  'prompt',
  'raw',
  'summary',
  'text',
  'title',
])

// Properties and attributes whose string values are machinery, never prose.
// `className` is a CSS class list, `value` is the half of a typed UI structure
// that the backend matches on while its sibling key carries the label, and the
// rest name things rather than say them.
const MACHINERY_PROPERTIES = new Set([
  'className',
  'class',
  'id',
  'key',
  'type',
  'value',
  'data-testid',
  'href',
  'src',
])

// Calls whose string arguments are selectors, queries or comparison targets
// rather than language. The comparison methods are here because a literal
// being *matched against* is never rendered - `badge.includes('free
// cancellation')` is reading supplier data, not addressing the shopper.
const MACHINERY_CALLS = new Set([
  'matchMedia',
  'querySelector',
  'querySelectorAll',
  'getElementById',
  'setAttribute',
  'getItem',
  'setItem',
  'removeItem',
  'includes',
  'startsWith',
  'endsWith',
  'indexOf',
])

// Language names are written in their own language in every interface -
// `Tiếng Việt` is correct on an English page and a Japanese one. Translating
// an endonym would make the switcher unreadable to the very shopper looking
// for their language in it.
const ENDONYM_DECLARATIONS = new Set(['LOCALE_NAMES'])

// The dictionary itself. Every entry here is a translation or the English
// source of one, so the file is the destination for findings rather than a
// place that can contain them.
const DICTIONARY = new Set(['src/lib/i18n.ts'])

// Sample catalogue content, not interface chrome. In production every one of
// these fields is served from the database already translated, so the English
// here is a fixture standing in for supplier data rather than a string the
// application is responsible for translating.
const CONTENT_FIXTURES = new Set(['src/data/demo.ts'])

// Operator tooling, not storefront. The console is used by staff who author in
// Vietnamese or English and it has its own language policy; the shopper-facing
// chrome gate does not speak for it. Listed explicitly, and the number of
// strings skipped is printed on every run, so this cannot quietly grow to
// cover a surface shoppers actually see.
const OPERATOR_ONLY = new Set(['src/components/AdminConsole.tsx'])

// Strings that are the same in every language. Proper nouns, brand marks and
// punctuation carry no translation obligation. Kept deliberately short: an
// allowlist is the escape hatch that quietly becomes the rule.
const ALLOWED = new Set(['VIETRA', 'Vietra', 'V'])

const isAllowed = (text) => {
  const trimmed = text.trim()
  if (!trimmed) return true
  if (ALLOWED.has(trimmed)) return true
  // No letters means nothing to translate: separators, counts, currency codes.
  if (!/\p{Letter}/u.test(trimmed)) return true
  return false
}

const walkFiles = (dir, out = []) => {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) walkFiles(full, out)
    else if (
      (entry.endsWith('.tsx') || entry.endsWith('.ts')) &&
      !DICTIONARY.has(relative(ROOT, full))
    )
      out.push(full)
  }
  return out
}

// Pulls the translatable literals out of an expression, following the shapes
// English actually hides in: a plain string, a template with substitutions
// (`Ask Mai about ${title}` - the sentence is in the spans), and both arms of
// a ternary, which is what a half-translated line looks like.
// Two or more words with letters in them. One word is a token - a filter
// value, a CSS class, an event name; a sentence needs at least two.
const isProse = (text) => {
  const trimmed = text.trim()
  if (isAllowed(trimmed)) return false
  const words = trimmed.split(/\s+/).filter((word) => /\p{Letter}{2,}/u.test(word))
  return words.length >= 2
}

// Whether a template builds a machine string rather than a sentence: a URL, a
// path, a CSS dimension, a storage key. These interpolate values into text too,
// but none of the text is language.
const CSS_UNITS = /^(px|rem|em|%|vh|vw|vmin|vmax|ms|s|deg|fr|ch|pt)$/
const isMachineryTemplate = (node) => {
  const parts = [node.head, ...node.templateSpans.map((span) => span.literal)]
  const joined = parts.map((part) => part.text).join(' ')
  if (/^[/.]|:\/\//.test(joined.trim())) return true
  // No whitespace in any part means the value is glued to a token rather than
  // placed in a sentence: `VT-${n}`, `${day}T00:00:00Z`, `demo-${id}`. A
  // sentence puts whitespace somewhere around the words it is made of.
  //
  // The limit of this rule is a language that does not use spaces, where a
  // real sentence could look like a token. It holds here because the checker
  // only ever reads English source; the translations live in the dictionary,
  // which the checker does not walk.
  if (!parts.some((part) => /\s/.test(part.text))) return true
  const words = joined.split(/[^\p{Letter}%]+/u).filter(Boolean)
  return words.length > 0 && words.every((word) => CSS_UNITS.test(word))
}

// What to print for a template, so the report shows the sentence rather than
// the fragment that happened to trip the rule.
const templateShape = (node) =>
  node.head.text +
  node.templateSpans.map((span) => '{}' + span.literal.text).join('')

// Whether a literal is an argument or value in a position that is structurally
// not language: a class list, a machinery-named property, or a DOM query.
const inMachineryContext = (node) => {
  const parent = node.parent
  if (!parent) return false
  // `new Error('...')` is diagnostic text for a log or a developer. Every
  // caller that shows something to a shopper catches and renders its own
  // translated message, so the thrown string never reaches a screen.
  if (ts.isNewExpression(parent) && ts.isIdentifier(parent.expression)) {
    if (parent.expression.text.endsWith('Error')) return true
  }
  if (
    ts.isPropertyAssignment(parent) &&
    ts.isVariableDeclaration(parent.parent?.parent ?? {}) &&
    ts.isIdentifier(parent.parent.parent.name) &&
    ENDONYM_DECLARATIONS.has(parent.parent.parent.name.text)
  ) {
    return true
  }
  if (
    (ts.isPropertyAssignment(parent) || ts.isJsxAttribute(parent)) &&
    (ts.isIdentifier(parent.name) || ts.isStringLiteral(parent.name)) &&
    MACHINERY_PROPERTIES.has(parent.name.text)
  ) {
    return true
  }
  if (ts.isJsxExpression(parent) && parent.parent) {
    return inMachineryContext(parent)
  }
  // `badge === 'Top pick'` reads supplier data to pick an icon; the literal is
  // a comparison target and is never rendered.
  if (ts.isBinaryExpression(parent)) {
    const op = parent.operatorToken.kind
    if (
      op === ts.SyntaxKind.EqualsEqualsEqualsToken ||
      op === ts.SyntaxKind.ExclamationEqualsEqualsToken ||
      op === ts.SyntaxKind.EqualsEqualsToken ||
      op === ts.SyntaxKind.ExclamationEqualsToken
    ) {
      return true
    }
    return inMachineryContext(parent)
  }
  if (ts.isConditionalExpression(parent)) {
    return inMachineryContext(parent)
  }
  if (ts.isCallExpression(parent)) {
    // `String(x ?? 'fallback')` is a conversion, so what matters is the
    // context the conversion itself sits in.
    if (ts.isIdentifier(parent.expression) && parent.expression.text === 'String') {
      return inMachineryContext(parent)
    }
    const callee = parent.expression
    const name = ts.isPropertyAccessExpression(callee)
      ? callee.name.text
      : ts.isIdentifier(callee)
        ? callee.text
        : ''
    if (MACHINERY_CALLS.has(name)) return true
    // Anything handed to `console.*` is written for whoever is reading the
    // browser log, which is never the shopper.
    if (
      ts.isPropertyAccessExpression(callee) &&
      ts.isIdentifier(callee.expression) &&
      callee.expression.text === 'console'
    ) {
      return true
    }
  }
  return false
}

const collectText = (expression, emit) => {
  if (!expression) return
  if (
    ts.isStringLiteral(expression) ||
    ts.isNoSubstitutionTemplateLiteral(expression)
  ) {
    if (!isAllowed(expression.text)) emit(expression)
  } else if (ts.isTemplateExpression(expression)) {
    if (!isAllowed(expression.head.text)) emit(expression.head)
    for (const span of expression.templateSpans) {
      if (!isAllowed(span.literal.text)) emit(span.literal)
    }
  } else if (ts.isConditionalExpression(expression)) {
    collectText(expression.whenTrue, emit)
    collectText(expression.whenFalse, emit)
  }
}

const findings = []
let operatorSkipped = 0
let fixtureSkipped = 0

for (const file of walkFiles(SRC)) {
  const relativePath = relative(ROOT, file)
  const operatorOnly = OPERATOR_ONLY.has(relativePath)
  const fixture = CONTENT_FIXTURES.has(relativePath)
  const source = ts.createSourceFile(
    file,
    readFileSync(file, 'utf8'),
    ts.ScriptTarget.Latest,
    true,
    file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  )

  const report = (node, text) => {
    if (operatorOnly) {
      operatorSkipped += 1
      return
    }
    if (fixture) {
      fixtureSkipped += 1
      return
    }
    const { line } = source.getLineAndCharacterOfPosition(node.getStart(source))
    findings.push({
      file: relativePath,
      line: line + 1,
      text: text.trim().replace(/\s+/g, ' ').slice(0, 60),
    })
  }

  const visit = (node) => {
    // Text sitting directly in markup: <span>Save voucher</span>
    if (ts.isJsxText(node) && !isAllowed(node.text)) {
      report(node, node.text)
    }

    // A bare string inside braces used as element *content*:
    // <span>{'Save voucher'}</span>, and the string arms of a ternary, which
    // is how half-translated components usually look:
    // {voucher ? 'Booked' : t('checkout.review')}
    //
    // Only when the container is a child of an element. The same syntax in an
    // attribute is usually className={busy ? 'active' : ''} - machinery, not
    // language - and attribute text is handled by TEXT_ATTRIBUTES below.
    if (
      ts.isJsxExpression(node) &&
      node.expression &&
      node.parent &&
      (ts.isJsxElement(node.parent) || ts.isJsxFragment(node.parent))
    ) {
      collectText(node.expression, (literal) => report(literal, literal.text))
    }

    // A text-bearing property in an object literal:
    //   { label: 'Questions before you book?' }
    // regardless of which file it sits in or how far it travels before it is
    // rendered. Also covers `{ raw: 'English' }`, which would otherwise be a
    // hole straight through `LocalizedText`.
    if (
      ts.isPropertyAssignment(node) &&
      (ts.isIdentifier(node.name) || ts.isStringLiteral(node.name)) &&
      TEXT_PROPERTIES.has(node.name.text)
    ) {
      collectText(node.initializer, (literal) =>
        report(literal, literal.text),
      )
    }

    // A sentence written anywhere at all.
    //
    // The rules above find strings by where they sit - in markup, or under a
    // property named `label`. Three times now, English has been found sitting
    // somewhere neither rule looks: a local array joined into a summary line, a
    // module-level constant, a ternary inside a `.map`. What all of those have
    // in common is not their position but their shape: they are prose. Two or
    // more words is prose; `wheelchair`, `ADD_TO_CART` and `active` are not.
    //
    // So this asks the opposite question. Instead of listing the places text
    // can appear, it treats every multi-word literal as text and excludes the
    // contexts that are demonstrably machinery: class lists, the `value` half
    // of a typed UI structure, and DOM/query calls.
    if (
      (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) &&
      isProse(node.text) &&
      !inMachineryContext(node)
    ) {
      report(node, node.text)
    }

    // A sentence assembled around a value: `Heard: ${text}`, `${n} min`.
    //
    // The prose rule above cannot see these. A template's text is split across
    // its head and spans, so `${minutes} min` is never a multi-word literal in
    // any single node - and `Tell me more about ${title}.` is a whole sentence
    // the gate certified as translated. A template that interpolates a value
    // into words is a sentence being built, so any letter-bearing part of it
    // is display text, however short. `min` and `h` are exactly the words that
    // must change for a Vietnamese shopper.
    if (
      ts.isTemplateExpression(node) &&
      !inMachineryContext(node) &&
      !isMachineryTemplate(node)
    ) {
      const parts = [node.head, ...node.templateSpans.map((span) => span.literal)]
      const spoken = parts.find((part) => /\p{Letter}/u.test(part.text))
      if (spoken) report(spoken, templateShape(node))
    }

    // Attribute values a shopper reads: aria-label="Close"
    if (ts.isJsxAttribute(node) && ts.isIdentifier(node.name)) {
      const name = node.name.text
      if (TEXT_ATTRIBUTES.has(name) && node.initializer) {
        const initializer = node.initializer
        if (ts.isStringLiteral(initializer) && !isAllowed(initializer.text)) {
          report(initializer, initializer.text)
        }
        if (ts.isJsxExpression(initializer) && initializer.expression) {
          const expression = initializer.expression
          if (ts.isStringLiteral(expression) && !isAllowed(expression.text)) {
            report(expression, expression.text)
          }
          if (ts.isTemplateExpression(expression)) {
            if (!isAllowed(expression.head.text)) {
              report(expression.head, expression.head.text)
            }
            for (const span of expression.templateSpans) {
              if (!isAllowed(span.literal.text)) {
                report(span.literal, span.literal.text)
              }
            }
          }
        }
      }
    }

    ts.forEachChild(node, visit)
  }

  visit(source)
}

const skipped = [
  operatorSkipped ? `${operatorSkipped} operator-console` : '',
  fixtureSkipped ? `${fixtureSkipped} demo-fixture` : '',
].filter(Boolean)
const skipNote = skipped.length
  ? ` (${skipped.join(', ')} string(s) skipped by policy)`
  : ''

if (findings.length) {
  console.error(
    `${findings.length} shopper-facing string(s) are not translatable.${skipNote}\n` +
      'Move each into src/lib/i18n.ts and render it with t(...):\n',
  )
  for (const finding of findings) {
    console.error(`  ${finding.file}:${finding.line}  "${finding.text}"`)
  }
  process.exit(1)
}

console.log(
  `i18n: every shopper-facing string is translatable.${skipNote}`,
)
