import re

from pylatex import Command, LongTable, MultiColumn, NoEscape

from osdagbridge.core.utils.common import (
    KEY_MP_GD_MEMBER_ID,
    KEY_MP_GD_SELECT_GIRDER,
    KEY_MP_GIRDER_DEPTH,
    KEY_TS_NO_OF_GIRDERS
)


# A row ending (\\ or \\[dim]) followed by \cline means the next row continues
# a \multirow group (e.g. one girder's block of rows).
_GROUP_ROW_END = re.compile(r"\\\\(?!\*)(\[[^\]]*\])?(\s*\\cline\{[^}]*\})")

_ROW_END_DIM = re.compile(r"\s*\[[^\]]*\]")

# Column types with an explicit width argument: p/m/b{w} and the report's C/L/R{w}.
_COL_WIDTH = re.compile(r"[pmbCLR]\{([^}]*)\}")


def _table_width_tex(col_spec, ncols):
    r"""TeX expression for the full width of a table, or None when a column has
    no fixed width (l/c/r). Width = column widths + 2\tabcolsep per column
    + one \arrayrulewidth per vertical rule."""
    widths = _COL_WIDTH.findall(re.sub(r">\{[^{}]*(\{[^{}]*\}[^{}]*)*\}", "", col_spec))
    if len(widths) != ncols:
        return None
    rules = col_spec.count("|")
    return (r"\dimexpr " + "+".join(widths)
            + rf"+{2 * ncols}\tabcolsep+{rules}\arrayrulewidth\relax")


def _first_group(body_tex):
    r"""Return the body's first row group: everything up to and including the
    first row end that allows a page break (``\\``, not ``\\*``).
    ``\\`` inside braces (e.g. ``\makecell{A\\B}``) is ignored."""
    depth, i = 0, 0
    while i < len(body_tex):
        ch = body_tex[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif body_tex.startswith("\\\\", i) and depth == 0:
            if body_tex.startswith("\\\\*", i):
                i += 3
                continue
            end = i + 2
            dim = _ROW_END_DIM.match(body_tex, end)
            return body_tex[:dim.end() if dim else end]
        elif ch == "\\":
            i += 1  # skip the escaped character (e.g. \{ or \&)
        i += 1
    return body_tex


def _reserve_space(col_spec, head_tex, first_group, caption):
    r"""LaTeX that starts a new page unless the table's caption, head and first
    row group fit on the current page.

    longtable only checks that the head and a single-line row fit. When the
    first row is taller (wrapped cells, a \multirow group) the page breaks
    inside the table before the first row, and the "continued" head is printed
    above the first-page head. Measuring the real height avoids that.

    \needspace is deliberately not used: it leaves a preferred break point
    (\penalty-100 with stretch) before the table, which TeX later picks while
    the table is being set, so longtable prints its foot and repeated head
    around an empty break.
    """
    caption_tex = (r"\setbox2=\vbox{\hsize=\LTcapwidth\raggedright\noindent"
                   r"\textbf{Table 0.00: " + caption + "}}%\n") if caption else r"\setbox2=\vbox{}%" + "\n"
    return ("\\par\\penalty10000\n"                # update \pagetotal; not a break point
            "\\begingroup\\let\\markSD\\relax\n"   # measuring must not set page flags
            r"\setbox0=\hbox{\begin{tabular}{" + col_spec + "}%\n"
            + head_tex + "\n" + first_group + "\n"
            r"\end{tabular}}%" "\n"
            + caption_tex +
            r"\dimen0=\dimexpr\ht0+\dp0+\ht2+\dp2+\LTpre+2\baselineskip\relax" "\n"
            r"\dimen2=\pagegoal \advance\dimen2 -\pagetotal" "\n"
            r"\ifdim\dimen0>\dimen2 \newpage\fi" "\n"
            r"\endgroup" "\n")


def make_longtable(col_spec, headers=None, body="", *, caption=None, label=None,
                   header_rows=None, ncols=None, header_gap="6pt",
                   continued_note=True, keep_row_groups=True, note=None):
    r"""Build a page-breakable table whose header repeats on every page.

    Every report table goes through this helper, so header repetition is
    handled in one place instead of per table.

    Layout produced (PyLaTeX ``LongTable``)::

        caption + header            -> \endfirsthead  (first page)
        caption (continued) + header -> \endhead       (every later page)
        "Continued on next page"    -> \endfoot       (every page but the last)
        (nothing)                   -> \endlastfoot   (last page)
        body rows

    Parameters
    ----------
    col_spec : str
        Raw LaTeX column spec, e.g. ``r"|C{2.5cm}|L{8cm}|p{5cm}|"``.
    headers : list[str] | None
        Column titles (LaTeX-safe); each is set in bold. ``None`` for a
        key/value table without a title row.
    body : str | list[list[str]]
        Either pre-built LaTeX rows (each ending in ``\\`` and ``\hline``)
        or a list of rows whose cells are LaTeX-safe strings.
    caption : str | None
        Caption text (LaTeX-safe); set in bold. Repeated as
        "(continued)" on later pages without a second List of Tables entry.
    header_rows : str | None
        Raw LaTeX for a multi-level header (``\multirow``, ``\multicolumn``,
        ``\cline``), used verbatim instead of ``headers`` on every page.
        Must not include the outer ``\hline`` rules.
    ncols : int | None
        Number of columns. Required with ``header_rows`` or when
        ``headers`` is ``None``. PyLaTeX cannot count custom column types
        such as ``C{..}`` / ``L{..}``, so the width is always passed in.
    header_gap : str | None
        Extra space after the title row (``\\[6pt]``); ``None`` for none.
    continued_note : bool
        Print "Continued on next page" at the bottom of broken pages.
    keep_row_groups : bool
        Keep rows joined by ``\cline`` (a ``\multirow`` group such as one
        girder's block) on the same page, so a group is never split from
        its label.
    note : str | None
        Text printed in italics under the table (LaTeX-safe, without the
        "Note:" prefix). It is part of the table's last-page foot, so it
        always stays on the same page as the last row.
    """
    if ncols is None:
        if not headers:
            raise ValueError("make_longtable: pass ncols when headers is not given")
        ncols = len(headers)
    if headers and not header_rows and len(headers) != ncols:
        raise ValueError(
            f"make_longtable: {len(headers)} headers for {ncols} columns")

    table = LongTable(NoEscape(col_spec), width=ncols)

    if header_rows:
        title_row = header_rows.strip()
    elif headers:
        title_row = " & ".join(rf"\textbf{{{h}}}" for h in headers)
        title_row += rf" \\[{header_gap}]" if header_gap else r" \\"
    else:
        title_row = None

    def _add_head(cap):
        if cap:
            table.append(NoEscape(cap + r"\\"))
        table.add_hline()
        if title_row:
            table.append(NoEscape(title_row))
            table.add_hline()

    # 1) Head on the first page.
    first_cap = None
    if caption:
        first_cap = Command("caption", NoEscape(rf"\textbf{{{caption}}}")).dumps()
        if label:
            first_cap += Command("label", NoEscape(label)).dumps()
    _add_head(first_cap)
    table.append(NoEscape(r"\endfirsthead"))

    # 2) Head repeated at the top of every following page.
    cont_cap = (rf"\caption[]{{\textbf{{{caption}}} \textit{{(continued)}}}}"
                if caption else None)
    _add_head(cont_cap)
    table.end_table_header()

    # 3) Foot on every page except the last.
    if continued_note:
        table.add_row((MultiColumn(ncols, align="r",
                                   data=NoEscape(r"\textit{\small Continued on next page}")),))
    table.end_table_footer()

    # 4) Foot on the last page: the body already ends with a rule; add the
    #    note here so it can never be separated from the table by a page break.
    note_after = ""
    if note:
        width = _table_width_tex(col_spec, ncols)
        if width:
            table.add_row((MultiColumn(
                ncols, align=NoEscape(r"@{}>{\raggedright\arraybackslash}p{" + width + "}@{}"),
                data=NoEscape(r"\rule{0pt}{3ex}\textit{Note: " + note + "}")),))
        else:
            # Columns without a fixed width: fall back to a note after the table.
            note_after = r"\noindent\textit{Note: " + note + "}\n"
    table.end_table_last_footer()

    # 5) Body rows.
    if isinstance(body, str):
        body_tex = body.strip()
    else:
        body_tex = "\n".join(" & ".join(str(c) for c in row) + "\\\\\n\\hline"
                             for row in body)
    if body_tex:
        if keep_row_groups:
            # No break after a grouped row (\\*). The report preamble patches
            # \cline so no break is allowed after the rule row either.
            body_tex = _GROUP_ROW_END.sub(
                lambda m: r"\\*" + (m.group(1) or "") + m.group(2), body_tex)
        if not body_tex.endswith(r"\hline"):
            body_tex += "\n\\hline"
        table.append(NoEscape(body_tex))

    # Start the table on a new page unless its head and first row group fit.
    head_tex = r"\hline" + ("\n" + title_row + "\n" + r"\hline" if title_row else "")
    reserve = _reserve_space(col_spec, head_tex, _first_group(body_tex), caption)
    return reserve + table.dumps() + ("\n" + note_after if note_after else "")


def _tex(value):
    """Escape a Python value for safe LaTeX embedding."""
    s = str(value) if value is not None else ''
    if not s:
        return r''
    # Normalise non-ASCII glyphs from section designations (e.g. "∠ 100 ⅹ 100ⅹ 10")
    # that pdflatex cannot render.
    for uni, ascii_ in [('∠', 'L'), ('ⅹ', 'x'), ('×', 'x')]:
        s = s.replace(uni, ascii_)
    s = s.replace('\\', r'\textbackslash{}')
    for ch, esc in [('&', r'\&'), ('%', r'\%'), ('$', r'\$'), ('#', r'\#'),
                    ('{', r'\{'), ('}', r'\}'),          
                    ('_', r'\_\allowbreak{}'),            
                    ('~', r'\textasciitilde{}'), ('^', r'\^{}')]:
        s = s.replace(ch, esc)
    s = s.replace(':', r':\allowbreak{}')
    return s


def _render_value(source_dict, key, unit=""):
    val = source_dict.get(key)
    if val in ("", None):
        return ""
    return _tex(val) + unit


def get_girder_entries(input_dict):
    """
    Retrieve all girder labels and member IDs from backend keys.

    Usage Example:
    --------------------------
    girder_entries = get_girder_entries(bridge.input_dict)
    
    # 1. Fallback handling (if backend hasn't populated keys yet)
    if not girder_entries:
        n = int(bridge.input_dict.get(KEY_TS_NO_OF_GIRDERS, 1))
        girder_entries = [(f"Girder {i}", f"M1") for i in range(1, n + 1)]
        
    # 2. Get total number of girders safely
    n_girders = len(girder_entries)
    
    # 3. Iterate over the girders to build table rows
    for lbl, mid in girder_entries:
        # lbl will be e.g., "G1", mid will be e.g., "G1M1"
        # Access girder specific keys dynamically:
        # val = input_dict.get(f"{KEY_MP_GIRDER_DEPTH}.{lbl}.{mid}")
        pass

    Returns:
        List[Tuple[str, str]]
    """
    n = int(input_dict.get(KEY_TS_NO_OF_GIRDERS, 0))

    entries = []

    for i in range(1, n + 1):
        entries.append(
            (
                input_dict.get(f"{KEY_MP_GD_SELECT_GIRDER}.G{i}", ""),
                input_dict.get(f"{KEY_MP_GD_MEMBER_ID}.G{i}.M1", ""),
            )
        )

    return entries


def _fig_or_placeholder(path, caption, width=r'0.9\textwidth'):
    """Embed figure if path is provided (file already copied to assets), else show placeholder box.
    path is the relative path as pdflatex will see it (e.g. 'assets/plan.png').
    """
    if path:
        p = path.replace('\\', '/')
        return (r'\begin{figure}[H]' + '\n'
                r'\centering' + '\n'
                r'\includegraphics[width=' + width + ']{' + p + '}\n'
                r'\caption*{' + caption + '}\n'
                r'\end{figure}')
    return (r'\begin{figure}[H]' + '\n'
            r'\centering' + '\n'
            r'\fbox{\parbox{0.97\textwidth}{' + '\n'
            r'\textit{[ PLACEHOLDER: ' + caption + r' ]}' + '\n'
            r'}}' + '\n'
            r'\caption*{' + caption + '}\n'
            r'\end{figure}')

def _fig_embed(path, caption, width=r'\textwidth', height=None):
    """Embed a real figure when path is provided (already copied); otherwise use an fbox placeholder."""
    if path:
        p = path.replace('\\', '/')
        opts = 'width=' + width
        if height:
            opts += ',height=' + height + ',keepaspectratio'
        return (r'\begin{figure}[H]' + '\n'
                r'\vspace{-0.5em}' + '\n'
                r'\centering' + '\n'
                r'\includegraphics[' + opts + ']{' + p + '}\n'
                r'\vspace{0.5em}' + '\n'
                r'\caption*{\small ' + caption + '}\n'
                r'\vspace{-0.5em}' + '\n'
                r'\end{figure}')
    # fbox placeholder — matches template exactly
    return (r'\noindent\fbox{\parbox{0.97\textwidth}{' + '\n'
            r'\textit{[ PLACEHOLDER: ' + caption + r' ]}' + '\n'
            r'}}')


