(function () {
  // This shim allows knowledgeData.js to be included 100% verbatim below.
  // The source file mixes two authoring patterns:
  //   window.KNOWLEDGE_DATA.courses["X"] = {...}   (incremental)
  //   window.KNOWLEDGE_DATA = { courses: {...}, buildEngine: [...] }  (full reassignment)
  // The second pattern, used verbatim, would silently wipe out courses/build
  // entries registered earlier in the file. This shim intercepts every
  // assignment to window.KNOWLEDGE_DATA and merges instead of overwriting,
  // so no course, module, mechanics, or analogy text is ever lost.
  var allCourses = {};
  var allBuildEngine = [];

  function makeKD() {
    return {
      courses: new Proxy({}, {
        set: function (target, prop, value) {
          allCourses[prop] = value;
          target[prop] = value;
          return true;
        }
      }),
      buildEngine: {
        push: function () {
          for (var i = 0; i < arguments.length; i++) allBuildEngine.push(arguments[i]);
          return allBuildEngine.length;
        }
      }
    };
  }

  var _kd = makeKD();

  Object.defineProperty(window, "KNOWLEDGE_DATA", {
    configurable: true,
    get: function () {
      return _kd;
    },
    set: function (v) {
      if (v && v.courses) {
        Object.keys(v.courses).forEach(function (k) {
          allCourses[k] = v.courses[k];
        });
      }
      if (v && v.buildEngine) {
        v.buildEngine.forEach(function (b) {
          allBuildEngine.push(b);
        });
      }
      _kd = makeKD();
    }
  });

  window.__finalizeCoreConceptsData = function () {
    window.CORE_CONCEPTS_DATA = { courses: allCourses, buildEngine: allBuildEngine };
  };
})();
