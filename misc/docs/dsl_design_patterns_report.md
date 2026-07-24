# Design Patterns for Declarative DSLs & Configuration Languages

*Principles, Patterns, Evolution Strategies, and a Domain-Specific Case Study*

*Prepared for Thor Whalen · March 2026*

---

## Part 1: General Principles of DSL / Configuration Language Design

### The Fundamental Tension

Every configuration language lives on a spectrum between **data** and **code**. The central insight, articulated by Tim Berners-Lee as the **Principle of Least Power** [1], is: use the least powerful language that gets the job done. The more powerful your DSL, the harder it is to analyze, validate, optimize, and version.

This connects to what some call the **Configuration Complexity Clock** [2]: projects often cycle through stages — hardcoded values → config file → DSL → embedded scripting → full programming language → back to hardcoded values — and the skill is knowing when to stop.

### Core Design Principles

#### 1. Declarative over imperative, until you can't

A declarative IR is analyzable, serializable, diffable, and transformable by tools. The moment you add conditionals, loops, or side effects, you've moved from "data you can reason about" to "programs you must execute to understand." Fowler calls this the distinction between **internal DSLs** (embedded in a host language) and **external DSLs** (standalone languages with their own parser) [3]. A Python fluent API (builders, chaining, `Mapping` facades) is an internal DSL; a YAML/JSON schema is an external one.

#### 2. Separate the semantic model from the expression

This is perhaps the most important principle. Fowler's architecture for DSLs [3] always has three layers:

- The **DSL expression** (what users write)
- A **Semantic Model** (the IR — the intermediate representation)
- The **execution/rendering backend**

The semantic model is the SSOT. Multiple front-ends (Python API, YAML, GUI) can produce it; multiple back-ends (video renderer, previewer, validator) can consume it.

#### 3. Closed for interpretation, open for extension

This is the configuration-language equivalent of the open-closed principle. See Part 3 for a full treatment.

#### 4. Make illegal states unrepresentable

Borrowed from the typed-FP world [4], this means your schema should structurally prevent nonsensical configurations. If a `voice_over` only makes sense when `captions` are present, the schema should enforce that — not a runtime validator.

#### 5. Composition over inheritance in schemas

Prefer composing small, reusable schema fragments over deep hierarchical schemas. Think of it as the schema equivalent of `meshed`'s DAG philosophy — each piece is independently testable and combinable.

#### 6. Referential transparency in configuration

Any "variable" or "template parameter" in your DSL should be substitutable without changing meaning. This is what makes your configs debuggable — you can always inline-expand everything and get a "fully resolved" document.

---

### Rules of Thumb

- **If you need `if/else`, you probably need two configs, not one config with branching.** Variants and overrides (layering) are almost always cleaner than conditionals inside a config.
- **If you need loops, you need a generation step, not a richer DSL.** Let Python (or whatever host) generate the repetitive parts, then emit a flat, loop-free IR.
- **Every field should have a sensible default or be required.** No "optional but breaks if missing" fields. This is the progressive-disclosure principle applied to schemas.
- **Strings are the enemy.** Every time you type `str` where you could use an enum, a typed reference, or a structured object, you're deferring a bug from schema-validation time to runtime.

---

### Gotchas

> **Greenspun's Tenth Rule for Config [5]:** "Any sufficiently complicated configuration language contains an ad hoc, informally-specified, bug-ridden, slow implementation of half of a programming language." Watch for this. The moment you add string interpolation + conditionals + includes, you're building a language runtime and should acknowledge it.

- **The "just YAML" trap:** YAML looks simple but has surprising semantics (the Norway problem: `NO` → `false`), no native schema, and no way to express computation. JSON Schema [6], CUE [7], or Dhall [8] exist precisely because raw YAML doesn't scale.
- **Premature abstraction in schemas:** Don't make everything parametric from day one. Start with concrete, verbose configurations that work. Extract parameters and templates only when you see real repetition across real use cases.

---

## Part 2: Questions to Ask for Your Specific Domain

### Universal Questions (ask for any DSL)

1. **Who writes it?** Developers? Domain experts? End users? This determines syntax complexity budget.
2. **Who/what reads it?** Only your renderer? Third-party tools? Humans debugging? This determines how "standard" your format needs to be.
3. **What's the lifecycle?** Write-once? Iterated over weeks? Generated then hand-edited? This determines how much you invest in diffability and merge-friendliness.
4. **What's the validation story?** Can you validate statically (schema), or do you need semantic validation (e.g., "this animation references a diagram element that exists")?
5. **What's the unit of reuse?** Can users define templates/components? If so, where does parameterization live?

### For the Educational Video DSL

This is a rich domain. Here are the pointed questions:

#### Content vs. Presentation Separation

- Can the same "lesson content" (concepts, sequence, captions) be rendered with different visual styles? If yes, you need a content layer and a style/theme layer — like HTML/CSS separation.
- Are diagrams defined by their semantic structure (nodes, edges, relationships) or by their visual layout (positions, colors)? Ideally both, with layout being derivable from structure + style rules.

#### Temporal Concerns

- Is timing **absolute** ("at 3.2s, show label X") or **relative** ("after the previous animation completes, show label X")? Relative timing is more composable and resilient to changes.
- Do you need a **timeline model** (like After Effects / Premiere — everything placed on tracks) or a **scene-graph model** (like a tree of nested scenes/steps, each with local time)? Scene graphs compose better; timelines give more precise control.
- Can scenes be reordered without breaking? If yes, minimize cross-scene temporal dependencies.

#### Parametric Diagrams

- What's the parameter space? Simple scalar substitution (`radius=5`)? Structural variation (different numbers of nodes)? Conditional elements (show/hide based on parameter)?
- Consider: parameters as a **context dict** passed down through the scene tree, with each node pulling what it needs. This is essentially dependency injection for config.

#### Voice and Captions

- Are captions a transcript of voice, or independent? If derived, one should be the SSOT and the other generated.
- How do you sync? Timestamp-based alignment? Or structural alignment ("this caption corresponds to this scene step")?

#### Suggested Layer Architecture

```
CourseSpec           — curriculum structure, learning objectives
  └─ LessonSpec      — sequence of concepts
      └─ SceneSpec   — one visual "beat"
          ├─ DiagramSpec(params)   — what to show
          ├─ AnimationSpec         — how it appears/transforms
          ├─ NarrationSpec         — what to say
          └─ CaptionSpec           — what to display as text
```

Each layer should be independently validatable and independently substitutable.

---

## Part 3: Evolution, Versioning, and the Open-Closed Principle for Schemas

### The Open-Closed Principle for Configuration

A schema is **open for extension, closed for modification** when:

- **New fields can be added** without breaking existing documents (additive-only changes).
- **Existing fields never change meaning** (semantic stability).
- **Old documents remain valid** under new schema versions (backward compatibility).
- **New documents can degrade gracefully** when read by old consumers (forward compatibility).

Protocol Buffers [9] got this right decades ago with simple rules: never reuse field numbers, never change field types, always make new fields optional with defaults.

### Versioning Strategies

#### 1. Envelope Pattern with Explicit Version

```json
{
    "version": "2.1",
    "kind": "LessonSpec",
    "spec": { "..." }
}
```

This is what Kubernetes uses. The `version` field tells the consumer which schema to apply. You can maintain multiple schema versions simultaneously.

#### 2. Additive Versioning (preferred for most cases)

Don't version at all — instead, make every change additive. New optional fields with defaults. Old consumers ignore unknown fields. This works until you need a breaking change, at which point you bump the `kind` or introduce a new document type entirely.

#### 3. Migration Functions

```python
# Registry of version upgraders
_migrations = {
    ("1.0", "1.1"): migrate_1_0_to_1_1,
    ("1.1", "2.0"): migrate_1_1_to_2_0,
}


def upgrade(doc, target_version="latest"):
    """Chain migrations to bring any doc to target version."""
    while doc["version"] != target_version:
        next_version = _next_version(doc["version"])
        doc = _migrations[(doc["version"], next_version)](doc)
    return doc
```

This is the approach Django uses for database migrations, and it works well for configuration too. Each migration is a pure function from old schema to new schema.

#### 4. Schema as Code (recommended for your ecosystem)

Use Pydantic models (or dataclasses) as your schema definition. Version them in Git. Use Pydantic's validators for semantic checks. Generate JSON Schema from Pydantic for external tooling. This gives you type checking, IDE support, and a migration path all in one.

```python
class SceneSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")  # or "allow" for forward compat

    diagram: DiagramSpec
    animation: AnimationSpec = AnimationSpec()  # sensible default
    narration: NarrationSpec | None = None  # optional
    # v1.1 addition: backward compatible because it has a default
    caption: CaptionSpec | None = None
```

### What Makes Evolution Smooth

- **Never remove fields** — deprecate them (ignore on read, stop writing).
- **Never change field types** — add a new field with the new type.
- **Use `extra="allow"` (or `extra="ignore"`)** in Pydantic so old consumers don't choke on new fields.
- **Keep a canonical "fully resolved" form** — even if your DSL has templates, inheritance, and variables, always be able to produce a flat, fully-expanded document. This is the form you version and validate against.
- **Test roundtrip stability:** `parse(serialize(doc)) == doc` should always hold.

---

## Part 4: Recommended Reading and Research Directions

### Essential Reading

Fowler's DSL book [3] is the definitive treatment — read at least Part I (narrative) even if you skip the pattern catalog. For the schema evolution problem specifically, the Protobuf language guide on updating message types [9] is concise and battle-tested. The CUE language documentation [7] is worth studying as an example of a configuration language designed from first principles with validation, composition, and evolution in mind.

### Deep Research Directions

1. **Study CUE, Dhall, and Jsonnet** as examples of configuration languages that tried to solve the "more than data, less than code" problem. CUE [7] unifies types, values, and constraints into a single lattice-based system. Dhall [8] proves that a total (non-Turing-complete) language can be surprisingly expressive while remaining analyzable. Jsonnet [10] is Google's approach — essentially JSON + functions + imports.

2. **Study Manim and Motion Canvas** for your video DSL specifically. Manim [11] (3Blue1Brown's engine) is a Python internal DSL for mathematical animations. Motion Canvas [12] is the TypeScript equivalent. Both have solved the "parametric diagram + animation + timeline" problem, and studying their scene/animation model would directly inform your IR design.

3. **Schema evolution in practice:** Read how Avro [13] and Protobuf [9] handle schema evolution — they've been doing this at massive scale for years. The key insight from both: the schema is not in the document, it's alongside the document, and reader/writer schemas can differ with well-defined resolution rules.

4. **The "Language Workbench" concept** from Fowler [14] and JetBrains MPS [15] — tools that let you define DSLs with projectional editing (the user never sees raw syntax). This is relevant if you're thinking about GUIs that produce your IR.

5. **Algebraic data types for configuration:** Research how Dhall and tools like Pydantic discriminated unions (`Annotated[Union[...], Discriminator(...)]`) let you model "one of these kinds of thing" — essential for a video DSL where a scene element could be a diagram, animation, text overlay, etc.

---

## Part 5: A Meta-Framework for Your DSL Decisions

Here's a decision ladder for each feature your DSL might have. It is essentially the Principle of Least Power applied incrementally, and maps directly to the `meshed` philosophy — computation happens in the DAG of Python functions; the configuration is the data flowing between them.

```
For each "feature" your DSL might have, ask:

1. Can I express this as pure data (enum, literal, structured object)?
   → Yes: Do that. Stop here.
   → No: Continue.

2. Can I express this as parameterized data (template + variables)?
   → Yes: Do that, but keep substitution simple (no expressions).
   → No: Continue.

3. Can I express this as composition of data (references, includes)?
   → Yes: Do that, but enforce a DAG (no cycles).
   → No: Continue.

4. Do I truly need computation (conditionals, mapping, derived values)?
   → Yes: Push it to the host language (Python generation step).
         Emit a fully-resolved, computation-free IR.
   → RESIST adding computation to the DSL itself.
```

---

## References

| # | Reference |
|---|-----------|
| [1] | Berners-Lee, T. "The Rule of Least Power." W3C TAG Finding, 2006. [W3C](https://www.w3.org/2001/tag/doc/leastPower.html) |
| [2] | Atwood, J. "The Configuration Complexity Clock." Coding Horror, 2007. [Coding Horror](https://blog.codinghorror.com/the-configuration-complexity-clock/) |
| [3] | Fowler, M. *Domain-Specific Languages.* Addison-Wesley, 2010. [Fowler's DSL Guide](https://martinfowler.com/dsl.html) |
| [4] | Wlaschin, S. "Designing with Types: Making Illegal States Unrepresentable." 2013. [F# for Fun and Profit](https://fsharpforfunandprofit.com/posts/designing-with-types-making-illegal-states-unrepresentable/) |
| [5] | Greenspun, P. "Greenspun's Tenth Rule of Programming." [Wikipedia](https://en.wikipedia.org/wiki/Greenspun%27s_tenth_rule) |
| [6] | JSON Schema. [json-schema.org](https://json-schema.org/) |
| [7] | The CUE Language. [cuelang.org](https://cuelang.org/) |
| [8] | Dhall Configuration Language. [dhall-lang.org](https://dhall-lang.org/) |
| [9] | Google. "Protocol Buffers: Updating a Message Type." [Protobuf Docs](https://protobuf.dev/programming-guides/proto3/#updating) |
| [10] | Jsonnet. [jsonnet.org](https://jsonnet.org/) |
| [11] | Manim Community. [manim.community](https://www.manim.community/) |
| [12] | Motion Canvas. [motioncanvas.io](https://motioncanvas.io/) |
| [13] | Apache Avro: Schema Resolution. [Avro Spec](https://avro.apache.org/docs/current/specification/#schema-resolution) |
| [14] | Fowler, M. "Language Workbenches: The Killer-App for Domain Specific Languages?" 2005. [martinfowler.com](https://martinfowler.com/articles/languageWorkbench.html) |
| [15] | JetBrains MPS. [jetbrains.com/mps](https://www.jetbrains.com/mps/) |
