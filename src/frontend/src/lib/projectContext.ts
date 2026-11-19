export const PROJECT_CONTEXT_DIRECTORY = "project_context"

const PROJECT_CONTEXT_ARCHIVE_SUFFIX = /\.(?:tar\.gz|tgz|tar|zip)$/i

/** Preserve safe folder-relative paths without changing original file contents. */
function projectContextPath(file: File) {
  const path = (file.webkitRelativePath || file.name).replace(/\\/g, "/")
  if (
    !path ||
    path.startsWith("/") ||
    /^[a-z]:/i.test(path) ||
    // biome-ignore lint/suspicious/noControlCharactersInRegex: Reject control characters in uploaded paths.
    /[\u0000-\u001f\u007f]/.test(path) ||
    path.split("/").some((part) => !part || part === "." || part === "..")
  ) {
    throw new Error("Reference files must have safe relative paths")
  }
  return path
}

/** Place optional author materials under a shared stage context. */
export function projectContextUploads(files: File[]) {
  return files.map((file) => {
    const parts = projectContextPath(file).split("/")
    const filename = parts[parts.length - 1]
    if (PROJECT_CONTEXT_ARCHIVE_SUFFIX.test(filename)) {
      const stem = filename.replace(PROJECT_CONTEXT_ARCHIVE_SUFFIX, "")
      parts.splice(
        parts.length - 1,
        0,
        !stem || stem === "." || stem === ".." ? "archive" : stem,
      )
    }
    return {
      file,
      relativePath: `${PROJECT_CONTEXT_DIRECTORY}/${parts.join("/")}`,
    }
  })
}
