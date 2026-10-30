"""Starting points: well-known portals of every size, plus directory pages that list many more.

Seed portals are always treated as job portals (their homepage may be too JS-heavy to
classify), but their login status is still verified by the crawler. ``access_hint`` is only
used when the crawler cannot verify a site itself (blocked / robots.txt); exports mark those
rows as ``seed_hint`` rather than ``verified``.
"""

# (url, tier, access_hint)
SEED_PORTALS: list[tuple[str, str, str | None]] = [
    # --- big, global / national -------------------------------------------------------
    ("https://www.indeed.com/", "big", "public"),
    ("https://www.linkedin.com/jobs/", "big", "public"),
    ("https://www.glassdoor.com/", "big", "login_required"),
    ("https://www.monster.com/", "big", None),
    ("https://www.ziprecruiter.com/", "big", None),
    ("https://www.careerbuilder.com/", "big", None),
    ("https://www.simplyhired.com/", "big", None),
    ("https://www.dice.com/", "big", None),
    ("https://www.usajobs.gov/", "big", None),
    ("https://www.snagajob.com/", "big", None),
    ("https://wellfound.com/", "big", None),
    ("https://builtin.com/", "big", None),
    ("https://www.flexjobs.com/", "big", "login_required"),
    ("https://www.adzuna.com/", "big", None),
    ("https://www.talent.com/", "big", None),
    ("https://www.careerjet.com/", "big", None),
    ("https://jooble.org/", "big", None),
    ("https://www.jora.com/", "big", None),
    ("https://www.naukri.com/", "big", None),
    ("https://www.foundit.in/", "big", None),
    ("https://www.shine.com/", "big", None),
    ("https://www.timesjobs.com/", "big", None),
    ("https://internshala.com/", "big", None),
    ("https://apna.co/", "big", None),
    ("https://www.seek.com.au/", "big", None),
    ("https://www.seek.co.nz/", "big", None),
    ("https://www.reed.co.uk/", "big", None),
    ("https://www.totaljobs.com/", "big", None),
    ("https://www.cv-library.co.uk/", "big", None),
    ("https://www.stepstone.de/", "big", None),
    ("https://www.jobs.ch/", "big", None),
    ("https://www.infojobs.net/", "big", None),
    ("https://www.welcometothejungle.com/", "big", None),
    ("https://www.apec.fr/", "big", None),
    ("https://www.francetravail.fr/", "big", None),
    ("https://www.bayt.com/", "big", None),
    ("https://www.naukrigulf.com/", "big", None),
    ("https://www.jobstreet.com/", "big", None),
    ("https://www.pracuj.pl/", "big", None),
    ("https://www.jobs.cz/", "big", None),
    ("https://www.catho.com.br/", "big", None),
    ("https://www.computrabajo.com/", "big", None),
    ("https://www.occ.com.mx/", "big", None),
    ("https://www.jobbank.gc.ca/", "big", None),
    ("https://www.workopolis.com/", "big", None),
    ("https://www.careers24.com/", "big", None),
    ("https://www.pnet.co.za/", "big", None),
    ("https://www.jobberman.com/", "big", None),
    ("https://www.brightermonday.co.ke/", "big", None),
    ("https://www.rozee.pk/", "big", None),
    ("https://www.saramin.co.kr/", "big", None),
    ("https://www.mycareersfuture.gov.sg/", "big", None),
    ("https://glints.com/", "big", None),
    # --- medium / specialist -----------------------------------------------------------
    ("https://www.efinancialcareers.com/", "medium", None),
    ("https://www.idealist.org/", "medium", None),
    ("https://www.higheredjobs.com/", "medium", None),
    ("https://www.jobs.ac.uk/", "medium", None),
    ("https://www.hcareers.com/", "medium", None),
    ("https://www.mediabistro.com/", "medium", None),
    ("https://www.hirist.tech/", "medium", None),
    ("https://www.iimjobs.com/", "medium", None),
    ("https://www.instahyre.com/", "medium", None),
    ("https://cutshort.io/", "medium", None),
    ("https://landing.jobs/", "medium", None),
    ("https://arc.dev/", "medium", None),
    ("https://www.workatastartup.com/", "medium", None),
    ("https://climatebase.org/", "medium", None),
    ("https://startup.jobs/", "medium", None),
    ("https://himalayas.app/", "medium", None),
    ("https://remote.co/", "medium", None),
    ("https://web3.career/", "medium", None),
    # --- small / niche -----------------------------------------------------------------
    ("https://weworkremotely.com/", "small", None),
    ("https://remoteok.com/", "small", None),
    ("https://remotive.com/", "small", None),
    ("https://www.workingnomads.com/", "small", None),
    ("https://jobspresso.co/", "small", None),
    ("https://nodesk.co/", "small", None),
    ("https://justremote.co/", "small", None),
    ("https://dynamitejobs.com/", "small", None),
    ("https://euremotejobs.com/", "small", None),
    ("https://4dayweek.io/", "small", None),
    ("https://authenticjobs.com/", "small", None),
    ("https://www.python.org/jobs/", "small", None),
    ("https://larajobs.com/", "small", None),
    ("https://vuejobs.com/", "small", None),
    ("https://rubyonremote.com/", "small", None),
    ("https://golangprojects.com/", "small", None),
    ("https://cryptojobslist.com/", "small", None),
    ("https://aijobs.net/", "small", None),
    ("https://berlinstartupjobs.com/", "small", None),
    ("https://jobs.gaijinpot.com/", "small", None),
    ("https://www.conservationjobboard.com/", "small", None),
    ("https://hasjob.co/", "small", None),
]

# Pages that list many job sites; every outbound site on them becomes a candidate.
DIRECTORY_PAGES = [
    "https://en.wikipedia.org/wiki/List_of_employment_websites",
    "https://raw.githubusercontent.com/lukasz-madon/awesome-remote-job/master/README.md",
    "https://raw.githubusercontent.com/tramcar/awesome-job-boards/master/README.md",
    "https://raw.githubusercontent.com/emredurukn/awesome-job-boards/master/README.md",
]

# Search queries (only with --search) for finding smaller, niche and regional boards.
NICHE_TERMS = [
    "remote", "tech", "developer", "data science", "design", "ux", "marketing", "healthcare",
    "nursing", "teaching", "higher education", "nonprofit", "climate", "government", "legal",
    "finance", "accounting", "hospitality", "construction", "trucking", "freelance", "internship",
    "graduate", "startup", "web3", "ai", "devops", "writing", "veterans", "women in tech",
]
REGION_TERMS = [
    "India", "UK", "Ireland", "Germany", "France", "Netherlands", "Spain", "Italy", "Poland",
    "Sweden", "Canada", "Australia", "New Zealand", "Singapore", "Malaysia", "Philippines",
    "Indonesia", "UAE", "Saudi Arabia", "Nigeria", "Kenya", "South Africa", "Brazil", "Mexico",
    "Argentina", "Japan", "Pakistan", "Bangladesh", "Vietnam", "Egypt",
]


def search_queries() -> list[str]:
    return ([f"{t} job board" for t in NICHE_TERMS]
            + [f"job portal {r}" for r in REGION_TERMS]
            + [f"niche job board {t}" for t in NICHE_TERMS[:10]])
