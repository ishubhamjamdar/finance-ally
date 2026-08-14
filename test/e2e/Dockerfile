# The Playwright runner. Browsers and their system libraries come from
# Microsoft's image; only this suite's own dependency is installed here.
#
# The tag must match `@playwright/test` in package.json exactly — the image
# ships browser builds that the library version expects, and a mismatch fails
# at run time with a message about a missing browser rather than at build.
# `test_e2e_versions_agree` in backend/tests/test_packaging.py pins the pair.
FROM mcr.microsoft.com/playwright:v1.62.1-noble

WORKDIR /e2e

# The lockfile alone first, so editing a spec does not reinstall.
COPY package.json package-lock.json ./
RUN npm ci

# The specs and config are mounted at run time by docker-compose.test.yml, so
# a failing assertion can be edited and re-run without a rebuild. They are
# copied here as well, so the image is runnable on its own.
COPY . .

# --forbid-only: an `.only` left in a spec silently reduces the suite to one
# test and still exits zero, which is the quietest way an E2E suite can stop
# testing anything.
CMD ["npx", "playwright", "test", "--forbid-only"]
