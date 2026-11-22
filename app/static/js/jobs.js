// The job queue screen.
//
// The table reloads itself every few seconds while something is running,
// which makes it easy to miss the moment a job changed. So before each
// swap we remember what every row said, and after it we flash the rows
// that say something different now.

(() => {
  let before = new Map();

  const read = () => {
    const seen = new Map();
    document.querySelectorAll("[data-job-id]").forEach((row) => {
      seen.set(row.dataset.jobId, row.dataset.jobStatus);
    });
    return seen;
  };

  document.body.addEventListener("htmx:beforeRequest", () => {
    if (document.querySelector("[data-job-id]")) before = read();
  });

  document.body.addEventListener("htmx:afterSettle", () => {
    if (!before.size) return;

    document.querySelectorAll("[data-job-id]").forEach((row) => {
      const was = before.get(row.dataset.jobId);
      // A row that was not there before is new, not changed: the arrival
      // animation already covers it.
      if (was === undefined || was === row.dataset.jobStatus) return;
      row.classList.add("row-flash");
      window.setTimeout(() => row.classList.remove("row-flash"), 1200);
    });

    before = read();
  });
})();
