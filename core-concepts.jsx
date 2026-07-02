// ============================================================================
// SECTION 2.4 — CORE CONCEPTS (auto-rendered from knowledgeData.js)
// ============================================================================

function parseIntro(introText) {
  if (!introText) return [];
  return introText.split("\n\n").map(block => {
    const firstNL = block.indexOf("\n");
    if (firstNL === -1) return { heading: null, lead: block, bullets: [] };
    const heading = block.slice(0, firstNL);
    let rest = block.slice(firstNL + 1);
    let bullets = [];
    if (rest.indexOf("\n• ") !== -1) {
      const parts = rest.split("\n• ");
      rest = parts[0];
      bullets = parts.slice(1).map(b => {
        const idx = b.indexOf(": ");
        if (idx > -1 && idx < 40) {
          return { title: b.slice(0, idx), body: b.slice(idx + 2) };
        }
        return { title: "", body: b };
      });
    }
    return { heading, lead: rest, bullets };
  });
}

function groupModulesBySection(modules) {
  const groups = [];
  let current = null;
  modules.forEach(mod => {
    const idx = mod.mechanics.indexOf("\n");
    let sectionLabel = "Core Concept";
    let body = mod.mechanics;
    if (idx > -1 && mod.mechanics.slice(0, idx).indexOf("Section") === 0) {
      sectionLabel = mod.mechanics.slice(0, idx);
      body = mod.mechanics.slice(idx + 1);
    }
    if (!current || current.label !== sectionLabel) {
      current = { label: sectionLabel, modules: [] };
      groups.push(current);
    }
    current.modules.push(Object.assign({}, mod, { mechanicsBody: body }));
  });
  return groups;
}

function ModuleRow({ module, isOpen, forceOpen, onToggle, highlighted }) {
  const open = isOpen || forceOpen;
  return (
    <div className={`border rounded-lg overflow-hidden ${highlighted ? "border-burnt-300 ring-1 ring-burnt-200" : "border-slate-200"}`}>
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between gap-3 px-3 py-2.5 text-left bg-white hover:bg-slate-50 transition-colors"
      >
        <div className="flex items-center gap-2.5 min-w-0">
          <span className="w-5 h-5 rounded bg-slate-100 text-slate-500 text-[10px] font-bold mono flex items-center justify-center shrink-0">
            {module.id}
          </span>
          <span className="text-[12.5px] font-semibold text-slate-800 truncate">{module.name}</span>
        </div>
        <span className={`text-slate-400 text-xs transition-transform shrink-0 ${open ? "rotate-180" : ""}`}>▼</span>
      </button>
      {open && (
        <div className="px-3 pb-3 pt-1 space-y-2 fade-in">
          <div className="bg-sky-50 border border-sky-200 rounded-lg p-3">
            <div className="text-[10.5px] font-semibold uppercase tracking-wide text-sky-700 mb-1">Core Mechanics</div>
            <p className="text-[12.5px] text-slate-700 leading-relaxed whitespace-pre-line">{module.mechanicsBody}</p>
          </div>
          <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-3">
            <div className="text-[10.5px] font-semibold uppercase tracking-wide text-emerald-700 mb-1">Real-World Analogy</div>
            <p className="text-[12.5px] text-slate-700 leading-relaxed whitespace-pre-line">{module.analogy}</p>
          </div>
        </div>
      )}
    </div>
  );
}

function CourseAccordion({ code, course, build, open, onToggle, query, openModules, toggleModule, moduleMatches }) {
  const introBlocks = useMemo(() => parseIntro(course.intro), [course.intro]);
  const groups = useMemo(() => groupModulesBySection(course.modules), [course.modules]);
  const domId = `concept-course-${code.replace(/\s+/g, "-")}`;
  return (
    <Card className="overflow-hidden" id={domId}>
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between gap-4 p-4 md:p-5 text-left hover:bg-slate-50 transition-colors"
      >
        <div className="flex items-center gap-4 min-w-0">
          <div className="w-16 h-10 rounded-lg bg-burnt-50 text-burnt-600 font-bold mono text-[11px] flex items-center justify-center shrink-0">
            {code}
          </div>
          <div className="min-w-0">
            <div className="text-sm font-semibold text-slate-900 truncate">{course.title}</div>
            <div className="text-[12px] text-slate-500 truncate">
              {course.modules.length} concept modules{build ? ` · Build: ${build.name}` : ""}
            </div>
          </div>
        </div>
        <span className={`text-slate-500 transition-transform shrink-0 ${open ? "rotate-180" : ""}`}>▼</span>
      </button>
      {open && (
        <div className="p-4 md:p-5 pt-0 fade-in space-y-6">
          {build && (
            <div className="text-[12px] text-burnt-700 bg-burnt-50 border border-burnt-200 rounded-lg px-3 py-2 leading-relaxed">
              <span className="font-semibold">{build.badge}: </span>
              {build.name} — {build.desc}
            </div>
          )}
          <div className="space-y-4">
            {introBlocks.map((b, i) => (
              <div key={i}>
                {b.heading && <div className="text-[13px] font-semibold text-slate-800 mb-1">{b.heading}</div>}
                {b.lead && <p className="text-[13px] text-slate-600 leading-relaxed whitespace-pre-line">{b.lead}</p>}
                {b.bullets.length > 0 && (
                  <div className="mt-2">
                    <BulletGroup items={b.bullets.map((bl, j) => ({ title: bl.title || `Point ${j + 1}`, body: bl.body }))} />
                  </div>
                )}
              </div>
            ))}
          </div>
          <div className="space-y-5">
            {groups.map((g, gi) => (
              <div key={gi}>
                <div className="text-[11px] font-semibold uppercase tracking-wide text-burnt-600 mb-2">{g.label}</div>
                <div className="space-y-2">
                  {g.modules.map(m => {
                    const key = `${code}-${m.id}`;
                    const isMatch = !!query && moduleMatches(course, m);
                    return (
                      <ModuleRow
                        key={m.id}
                        module={m}
                        isOpen={!!openModules[key]}
                        forceOpen={isMatch}
                        onToggle={() => toggleModule(key)}
                        highlighted={isMatch}
                      />
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function CoreConcepts() {
  const CC = window.CORE_CONCEPTS_DATA || { courses: {}, buildEngine: [] };
  const courseKeys = useMemo(() => Object.keys(CC.courses), [CC]);
  const [query, setQuery] = useState("");
  const [openCourses, setOpenCourses] = useState({});
  const [openModules, setOpenModules] = useState({});
  const q = query.trim().toLowerCase();

  const moduleMatches = (course, mod) => {
    if (!q) return false;
    return (
      mod.name.toLowerCase().indexOf(q) !== -1 ||
      mod.mechanics.toLowerCase().indexOf(q) !== -1 ||
      mod.analogy.toLowerCase().indexOf(q) !== -1
    );
  };
  const courseMatches = (code, course) => {
    if (!q) return false;
    if (
      course.title.toLowerCase().indexOf(q) !== -1 ||
      code.toLowerCase().indexOf(q) !== -1 ||
      course.intro.toLowerCase().indexOf(q) !== -1
    )
      return true;
    return course.modules.some(m => moduleMatches(course, m));
  };

  const totalModules = useMemo(() => courseKeys.reduce((sum, k) => sum + CC.courses[k].modules.length, 0), [CC, courseKeys]);
  const matchCount = q
    ? courseKeys.reduce((sum, k) => sum + CC.courses[k].modules.filter(m => moduleMatches(CC.courses[k], m)).length, 0)
    : 0;

  const toggleCourse = code => setOpenCourses(o => ({ ...o, [code]: !o[code] }));
  const toggleModule = key => setOpenModules(o => ({ ...o, [key]: !o[key] }));
  const expandAll = () => {
    const oc = {};
    courseKeys.forEach(k => (oc[k] = true));
    const om = {};
    courseKeys.forEach(k => CC.courses[k].modules.forEach(m => (om[`${k}-${m.id}`] = true)));
    setOpenCourses(oc);
    setOpenModules(om);
  };
  const collapseAll = () => {
    setOpenCourses({});
    setOpenModules({});
  };
  const jumpTo = code => {
    setOpenCourses(o => ({ ...o, [code]: true }));
    setTimeout(() => {
      const el = document.getElementById(`concept-course-${code.replace(/\s+/g, "-")}`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);
  };

  if (courseKeys.length === 0) {
    return (
      <div className="space-y-6 mt-14 pt-14 border-t border-slate-200" id="core-concepts">
        <SectionHeading
          kicker="2.4"
          title="Core Concepts"
          sub="Knowledge base data failed to load — check that knowledgeData.js is included on the page."
        />
      </div>
    );
  }

  return (
    <div className="space-y-6 mt-14 pt-14 border-t border-slate-200" id="core-concepts">
      <SectionHeading
        kicker="2.4"
        title="Core Concepts"
        sub={`Every UT Austin ECE course transcript, distilled into ${courseKeys.length} courses and ${totalModules} concept modules — each paired with the formal mechanics and a plain-English analogy for fast, structured reading.`}
      />

      <div className="flex flex-col md:flex-row gap-3 md:items-center md:justify-between">
        <div className="flex gap-2 flex-wrap">
          {courseKeys.map(code => (
            <button
              key={code}
              onClick={() => jumpTo(code)}
              className="px-2.5 py-1.5 rounded-lg text-[11px] font-medium border bg-white text-slate-600 border-slate-200 hover:border-burnt-300 hover:text-burnt-600 mono transition-colors"
            >
              {code}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={expandAll}
            className="px-3 py-2 rounded-lg text-xs font-medium border border-slate-200 bg-white text-slate-600 hover:border-slate-300"
          >
            Expand All
          </button>
          <button
            onClick={collapseAll}
            className="px-3 py-2 rounded-lg text-xs font-medium border border-slate-200 bg-white text-slate-600 hover:border-slate-300"
          >
            Collapse All
          </button>
        </div>
      </div>

      <div>
        <input
          type="text"
          placeholder="Search concepts, mechanics, analogies..."
          value={query}
          onChange={e => setQuery(e.target.value)}
          className="bg-white border border-slate-200 rounded-lg px-3.5 py-2 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:border-burnt-500/60 w-full"
        />
        {q && (
          <div className="text-[12px] text-slate-500 mt-2">
            {matchCount} module{matchCount === 1 ? "" : "s"} match "{query}" — matching courses auto-expand below.
          </div>
        )}
      </div>

      <div className="space-y-4">
        {courseKeys.map(code => (
          <CourseAccordion
            key={code}
            code={code}
            course={CC.courses[code]}
            build={CC.buildEngine.find(b => b.code === code)}
            open={!!openCourses[code] || courseMatches(code, CC.courses[code])}
            onToggle={() => toggleCourse(code)}
            query={q}
            openModules={openModules}
            toggleModule={toggleModule}
            moduleMatches={moduleMatches}
          />
        ))}
      </div>
    </div>
  );
}
