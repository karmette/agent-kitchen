package main

import (
	"fmt"
	"strings"
	"time"

	tea "charm.land/bubbletea/v2"
	lg "charm.land/lipgloss/v2"
)

type tickMsg time.Time

// ── Theme ───────────────────────────────────────────────────────────────────

var (
	purple     = lg.Color("#7D56F4")
	deepPurple = lg.Color("#5B3CC4")
	blue       = lg.Color("#3993ED")
	brightBlue = lg.Color("#96BEFD")
	cyan       = lg.Color("#56C8D8")
	green      = lg.Color("#81F88F")
	gold       = lg.Color("#FFD700")
	amber      = lg.Color("#FFB347")
	dimColor   = lg.Color("#444444")
	softDim    = lg.Color("#666666")
	textColor  = lg.Color("#CCCCCC")
	bgDark     = lg.Color("#0d0d1a")
	bgPanel    = lg.Color("#121228")

	outerBorder = lg.NewStyle().
			Border(lg.DoubleBorder()).
			BorderForeground(deepPurple).
			Padding(0, 1)

	outerBorderDone = lg.NewStyle().
				Border(lg.DoubleBorder()).
				BorderForeground(green).
				Padding(0, 1)

	cellNormal = lg.NewStyle().
			Border(lg.RoundedBorder()).
			BorderForeground(lg.Color("#333344"))

	cellFocused = lg.NewStyle().
			Border(lg.RoundedBorder()).
			BorderForeground(purple)

	cellDone = lg.NewStyle().
			Border(lg.RoundedBorder()).
			BorderForeground(lg.Color("#2a4a2a"))

	accent    = lg.NewStyle().Foreground(purple).Bold(true)
	blueText  = lg.NewStyle().Foreground(blue).Bold(true)
	cyanText  = lg.NewStyle().Foreground(cyan)
	dim       = lg.NewStyle().Foreground(dimColor)
	softText  = lg.NewStyle().Foreground(softDim)
	greenText = lg.NewStyle().Foreground(green)
	goldText  = lg.NewStyle().Foreground(gold).Bold(true)
	amberText = lg.NewStyle().Foreground(amber)
)

// ── Spinner ─────────────────────────────────────────────────────────────────

var spinnerFrames = []string{"⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"}

func spinner(_ int) string {
	ms := time.Now().UnixMilli()
	return spinnerFrames[(ms/150)%int64(len(spinnerFrames))]
}

// ── Types ───────────────────────────────────────────────────────────────────

type CellStatus string

const (
	StatusPending CellStatus = "·"
	StatusRunning CellStatus = "●"
	StatusDone    CellStatus = "✓"
	StatusError   CellStatus = "✗"
)

type agentMeta struct {
	generation int
	name       string // agent name (first negotiator seen in this group)
}

type cell struct {
	status     CellStatus
	conv       conversation
	content    strings.Builder              // first agent's conversation (for preview)
	agents     map[int]*strings.Builder     // group_id -> conversation content
	agentMetas map[int]*agentMeta           // group_id -> metadata
	agentIDs   []int                        // ordered group_ids as they appear
	scenario   string
	activeTab  int                          // which agent tab is selected in detail view
}

type ViewMode int

const (
	ModeGrid ViewMode = iota
	ModeDetail
	ModeResult
)

type ResultModel struct {
	rows, cols   int
	iterations   int
	focusedRow   int
	focusedCol   int
	width        int
	height       int
	cells        []cell
	mode         ViewMode
	detailScroll int
	resultScroll int
	content      string
	tick         int
}

func NewResultModel(rows, cols, iterations, numCells int) *ResultModel {
	cells := make([]cell, numCells)
	for i := range cells {
		cells[i].status = StatusPending
		cells[i].conv = newConversation()
		cells[i].agents = make(map[int]*strings.Builder)
		cells[i].agentMetas = make(map[int]*agentMeta)
	}
	return &ResultModel{
		rows: rows, cols: cols, iterations: iterations,
		cells: cells, mode: ModeGrid,
	}
}

func (m *ResultModel) Init() tea.Cmd {
	return tea.Tick(time.Millisecond*100, func(t time.Time) tea.Msg {
		return tickMsg(t)
	})
}

// ── Update ──────────────────────────────────────────────────────────────────

func (m *ResultModel) Update(msg tea.Msg) tea.Cmd {
	m.checkChannels()

	switch msg := msg.(type) {
	case tickMsg:
		m.tick++
		return tea.Tick(time.Millisecond*100, func(t time.Time) tea.Msg {
			return tickMsg(t)
		})
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
	case tea.KeyMsg:
		switch msg.String() {
		case "q", "ctrl+c":
			return tea.Quit
		case "enter", "o":
			if m.mode == ModeGrid {
				idx := m.focusedRow*m.cols + m.focusedCol
				if idx < len(m.cells) {
					m.mode = ModeDetail
					m.detailScroll = 0
				}
			}
		case "r":
			if FinalResult != nil {
				m.mode = ModeResult
				m.resultScroll = 0
			}
		case "esc":
			if m.mode != ModeGrid {
				m.mode = ModeGrid
			}
		case "h", "left":
			if m.mode == ModeDetail {
				cidx := m.focusedRow*m.cols + m.focusedCol
				if cidx < len(m.cells) && m.cells[cidx].activeTab > 0 {
					m.cells[cidx].activeTab--
					m.detailScroll = 0
				}
			} else if m.mode == ModeGrid {
				idx := m.focusedRow*m.cols + m.focusedCol - 1
				if idx < 0 {
					idx = len(m.cells) - 1
				}
				m.focusedRow = idx / m.cols
				m.focusedCol = idx % m.cols
			}
		case "l", "right":
			if m.mode == ModeDetail {
				cidx := m.focusedRow*m.cols + m.focusedCol
				if cidx < len(m.cells) && m.cells[cidx].activeTab < len(m.cells[cidx].agentIDs)-1 {
					m.cells[cidx].activeTab++
					m.detailScroll = 0
				}
			} else if m.mode == ModeGrid {
				idx := m.focusedRow*m.cols + m.focusedCol + 1
				if idx >= len(m.cells) {
					idx = 0
				}
				m.focusedRow = idx / m.cols
				m.focusedCol = idx % m.cols
			}
		case "k", "up":
			switch m.mode {
			case ModeDetail:
				m.detailScroll++
			case ModeResult:
				m.resultScroll++
			case ModeGrid:
				if m.focusedRow > 0 {
					m.focusedRow--
				}
			}
		case "j", "down":
			switch m.mode {
			case ModeDetail:
				if m.detailScroll > 0 {
					m.detailScroll--
				}
			case ModeResult:
				if m.resultScroll > 0 {
					m.resultScroll--
				}
			case ModeGrid:
				if m.focusedRow < m.rows-1 {
					m.focusedRow++
				}
			}
		}
	}
	return nil
}

func (m *ResultModel) checkChannels() {
	for i := range m.cells {
		if m.cells[i].status == StatusDone {
			continue
		}
		select {
		case msg := <-m.cells[i].conv.ch:
			m.cells[i].content.WriteString(msg)
			if m.cells[i].content.Len() > 100000 {
				s := m.cells[i].content.String()
				m.cells[i].content.Reset()
				m.cells[i].content.WriteString(s[len(s)-80000:])
			}
			if m.cells[i].status == StatusPending {
				m.cells[i].status = StatusRunning
			}
		case <-m.cells[i].conv.done:
			m.cells[i].status = StatusDone
		default:
		}
	}
}

// ── View ────────────────────────────────────────────────────────────────────

func (m *ResultModel) View() string {
	if m.width == 0 || m.height == 0 {
		return outerBorder.Render("Initializing...")
	}

	isDone := FinalResult != nil && m.allDone()
	container := outerBorder
	if isDone {
		container = outerBorderDone
	}
	container = container.Width(m.width).Height(m.height)
	iw := m.width - container.GetHorizontalFrameSize()
	ih := m.height - container.GetVerticalFrameSize()

	statusBar := m.viewStatusBar(iw)
	mainHeight := ih - 1

	var main string
	switch m.mode {
	case ModeDetail:
		main = m.viewDetail(iw, mainHeight)
	case ModeResult:
		main = m.viewResult(iw, mainHeight)
	default:
		main = m.viewGrid(iw, mainHeight)
	}

	// Pad main to full height so status bar is always at the bottom
	mainLines := strings.Count(main, "\n") + 1
	if mainLines < mainHeight {
		main += strings.Repeat("\n", mainHeight-mainLines)
	}

	return container.Render(main + "\n" + statusBar)
}

func (m *ResultModel) viewStatusBar(w int) string {
	left := ""
	if FinalResult != nil {
		left = greenText.Bold(true).Render(" ★ EVOLUTION COMPLETE ") +
			softText.Render(fmt.Sprintf("best: %.0f%%  ", FinalResult.BestScore*100))
	} else {
		gen := CurrentGeneration
		total := m.iterations
		left = accent.Render(fmt.Sprintf(" Gen %d/%d ", gen+1, total))
		barWidth := 15
		if total > 0 {
			filled := ((gen + 1) * barWidth) / total
			if filled > barWidth {
				filled = barWidth
			}
			left += accent.Render(strings.Repeat("▓", filled)) +
				dim.Render(strings.Repeat("░", barWidth-filled)) + " "
		}
		if CurrentBestScore > 0 {
			left += greenText.Render(fmt.Sprintf("best: %.0f%% ", CurrentBestScore*100))
		}
	}

	right := ""
	switch m.mode {
	case ModeGrid:
		right = softText.Render("enter: expand · ")
		if FinalResult != nil {
			right += accent.Render("r: results · ")
		}
		right += softText.Render("q: quit")
	case ModeDetail:
		right = softText.Render("↑↓: scroll · esc: back · q: quit")
	case ModeResult:
		right = softText.Render("↑↓: scroll · esc: back · q: quit")
	}

	gap := w - lg.Width(left) - lg.Width(right)
	if gap < 0 {
		gap = 0
	}
	return left + strings.Repeat(" ", gap) + right
}

// ── Grid view ───────────────────────────────────────────────────────────────

func (m *ResultModel) viewGrid(w, h int) string {
	sidePanelWidth := 28
	dividerWidth := 1
	gridWidth := w - sidePanelWidth - dividerWidth
	cellWidth := gridWidth / m.cols
	cellHeight := h / m.rows

	if cellWidth < 12 {
		cellWidth = 12
	}
	if cellHeight < 5 {
		cellHeight = 5
	}

	var gridRows []string
	for r := 0; r < m.rows; r++ {
		var rowCells []string
		for c := 0; c < m.cols; c++ {
			idx := r*m.cols + c
			if idx >= len(m.cells) {
				rowCells = append(rowCells, strings.Repeat(" ", cellWidth))
				continue
			}

			cell := m.cells[idx]
			style := cellNormal
			if r == m.focusedRow && c == m.focusedCol {
				style = cellFocused
			} else if cell.status == StatusDone {
				style = cellDone
			}

			// Header bar with scenario name
			statusStr := string(cell.status)
			if cell.status == StatusRunning {
				statusStr = spinner(m.tick)
			}
			name := cell.scenario
			if name == "" {
				name = fmt.Sprintf("Scenario %d", idx+1)
			}
			maxNameLen := cellWidth - 8
			if maxNameLen > 0 && len(name) > maxNameLen {
				name = name[:maxNameLen-1] + "…"
			}
			headerText := fmt.Sprintf(" %s %s ", statusStr, name)
			padLen := cellWidth - len([]rune(headerText)) - 2
			if padLen > 0 {
				headerText += strings.Repeat(" ", padLen)
			}
			headerColor := brightBlue
			if cell.status == StatusDone {
				headerColor = green
			}
			if r == m.focusedRow && c == m.focusedCol {
				headerColor = purple
			}
			header := lg.NewStyle().
				Background(bgPanel).
				Foreground(headerColor).
				Render(headerText)

			contentHeight := cellHeight - 4
			preview := m.styleCellPreview(cell.content.String(), cellWidth-4, contentHeight)

			styled := style.Width(cellWidth).Height(cellHeight).Render(header + "\n" + preview)
			rowCells = append(rowCells, styled)
		}
		gridRows = append(gridRows, lg.JoinHorizontal(lg.Top, rowCells...))
	}
	grid := lg.JoinVertical(lg.Top, gridRows...)

	// Vertical divider
	gridHeight := cellHeight * m.rows
	divider := strings.Repeat(dim.Render("│")+"\n", gridHeight)
	divider = strings.TrimRight(divider, "\n")

	side := m.viewSidePanel(sidePanelWidth, gridHeight)

	return lg.JoinHorizontal(lg.Top, grid, divider, side)
}

func (m *ResultModel) viewSidePanel(w, h int) string {
	var items []string

	brand := lg.NewStyle().Foreground(purple).Bold(true).Render("agent kitchen")
	items = append(items, brand)
	items = append(items, "")

	if FinalResult != nil {
		items = append(items, greenText.Bold(true).Render("✓ evolution complete"))
		items = append(items, greenText.Render(fmt.Sprintf("  best: %.0f%%", FinalResult.BestScore*100)))
		items = append(items, accent.Render("  r → view evolved agent"))
	} else {
		gen := CurrentGeneration
		total := m.iterations
		items = append(items, blueText.Render(fmt.Sprintf("Gen %d / %d", gen+1, total)))
		barWidth := w - 4
		if barWidth > 3 && total > 0 {
			filled := ((gen + 1) * barWidth) / total
			if filled > barWidth {
				filled = barWidth
			}
			bar := lg.NewStyle().Foreground(purple).Render(strings.Repeat("▓", filled)) +
				dim.Render(strings.Repeat("░", barWidth-filled))
			items = append(items, " "+bar)
		}
		if CurrentBestScore > 0 {
			items = append(items, greenText.Render(fmt.Sprintf("  best so far: %.0f%%", CurrentBestScore*100)))
		}
	}
	items = append(items, "")

	// Scenarios
	items = append(items, dim.Render("─ scenarios"))
	for i := 0; i < len(m.cells); i++ {
		name := m.cells[i].scenario
		if name == "" {
			name = fmt.Sprintf("Scenario %d", i+1)
		}
		maxLen := w - 5
		if len(name) > maxLen {
			name = name[:maxLen-1] + "…"
		}

		statusStr := string(m.cells[i].status)
		if m.cells[i].status == StatusRunning {
			statusStr = spinner(m.tick)
		}

		icon := dim.Render(statusStr)
		if m.cells[i].status == StatusRunning {
			icon = cyanText.Render(statusStr)
		} else if m.cells[i].status == StatusDone {
			icon = greenText.Render(statusStr)
		}

		isFocused := (i/m.cols == m.focusedRow && i%m.cols == m.focusedCol)
		if isFocused {
			items = append(items, accent.Render(fmt.Sprintf(" %s %s", statusStr, name)))
		} else {
			items = append(items, fmt.Sprintf(" %s %s", icon, softText.Render(name)))
		}
	}

	items = append(items, "")

	// Activity log
	items = append(items, dim.Render("─ activity"))

	maxLogs := h - len(items) - 1
	if maxLogs < 2 {
		maxLogs = 2
	}
	logs := ActivityLog
	if len(logs) > maxLogs {
		logs = logs[len(logs)-maxLogs:]
	}
	for _, l := range logs {
		if len(l) > w-3 {
			l = l[:w-4] + "…"
		}
		// Color-code activity entries
		if strings.HasPrefix(l, "  ") && strings.Contains(l, "→") {
			// Score line: "  abc123 → 65%"
			items = append(items, cyanText.Render(" "+l))
		} else if strings.HasPrefix(l, "──") {
			items = append(items, amberText.Render(" "+l))
		} else if strings.Contains(l, "done") || strings.Contains(l, "best:") {
			items = append(items, greenText.Render(" "+l))
		} else if strings.Contains(l, "survived") {
			items = append(items, greenText.Render(" "+l))
		} else {
			items = append(items, softText.Render(" "+l))
		}
	}

	content := lg.JoinVertical(lg.Top, items...)
	return lg.NewStyle().PaddingLeft(1).Width(w).Height(h).Render(content)
}

// ── Detail view ─────────────────────────────────────────────────────────────

func (m *ResultModel) viewDetail(w, h int) string {
	idx := m.focusedRow*m.cols + m.focusedCol
	if idx >= len(m.cells) {
		return "No cell selected"
	}

	cell := &m.cells[idx]
	name := cell.scenario
	if name == "" {
		name = fmt.Sprintf("Scenario %d", idx+1)
	}

	statusStr := string(cell.status)
	if cell.status == StatusRunning {
		statusStr = spinner(m.tick)
	}

	// Header
	headerText := fmt.Sprintf(" %s  %s ", name, statusStr)
	padLen := w - len([]rune(headerText))
	if padLen > 0 {
		headerText += strings.Repeat(" ", padLen)
	}
	headerColor := brightBlue
	if cell.status == StatusDone {
		headerColor = green
	}
	header := lg.NewStyle().
		Background(bgPanel).
		Foreground(headerColor).
		Render(headerText)

	// Tab bar — show generation + agent name per tab
	numAgents := len(cell.agentIDs)
	tabSection := ""
	usedLines := 2
	if numAgents > 0 {
		var tabs string
		for i := 0; i < numAgents; i++ {
			gid := cell.agentIDs[i]
			meta := cell.agentMetas[gid]
			label := fmt.Sprintf(" %d ", i+1)
			if meta != nil {
				name := meta.name
				if len(name) > 8 {
					name = name[:8]
				}
				if name != "" {
					label = fmt.Sprintf(" G%d·%s ", meta.generation, name)
				} else {
					label = fmt.Sprintf(" G%d·#%d ", meta.generation, i+1)
				}
			}
			if i == cell.activeTab {
				tabs += lg.NewStyle().
					Background(purple).
					Foreground(lg.Color("#FFFFFF")).
					Bold(true).
					Render(label) + " "
			} else {
				tabs += lg.NewStyle().
					Background(lg.Color("#222233")).
					Foreground(softDim).
					Render(label) + " "
			}
		}
		if numAgents > 1 {
			tabs += dim.Render("←→")
		}
		tabSection = tabs
		usedLines = 4
	}

	// Get the active agent's conversation
	var raw string
	if numAgents > 0 && cell.activeTab < numAgents {
		gid := cell.agentIDs[cell.activeTab]
		if buf, ok := cell.agents[gid]; ok {
			raw = buf.String()
		}
	} else {
		raw = cell.content.String()
	}

	styled := m.styleDetailView(raw, w-4)
	chat := m.scrollView(styled, h-usedLines, &m.detailScroll)

	if tabSection != "" {
		return header + "\n\n" + tabSection + "\n\n" + chat
	}
	return header + "\n\n" + chat
}

// ── Result view ─────────────────────────────────────────────────────────────

func (m *ResultModel) viewResult(w, h int) string {
	r := FinalResult
	if r == nil {
		return "No results"
	}

	topH := h * 55 / 100
	if topH < 12 {
		topH = 12
	}
	botH := h - topH - 2

	leftW := w * 55 / 100
	rightW := w - leftW

	// Left: header + chart
	var left strings.Builder
	left.WriteString(goldText.Render(" ★ EVOLUTION COMPLETE") + "\n")
	left.WriteString(softText.Render(fmt.Sprintf(" %s", r.Goal)) + "\n")
	left.WriteString(dim.Render(fmt.Sprintf(" %d agents × %d generations × %d scenarios",
		r.PopSize, len(r.Generations), len(r.ScenarioNames))) + "\n\n")

	improvement := 0.0
	if len(r.Generations) > 1 {
		first := r.Generations[0].BestScore
		if first > 0 {
			improvement = ((r.BestScore - first) / first) * 100
		}
	}
	left.WriteString(greenText.Bold(true).Render(fmt.Sprintf(" %.0f%%", r.BestScore*100)))
	left.WriteString(softText.Render(" final best score"))
	if improvement > 0 {
		left.WriteString(greenText.Render(fmt.Sprintf("  ↑%.0f%% improvement", improvement)))
	}
	left.WriteString("\n\n")

	if len(r.Generations) > 0 {
		left.WriteString(dim.Render(" Fitness Over Generations") + "\n")
		left.WriteString(dim.Render(" ") +
			lg.NewStyle().Foreground(purple).Render("█") + dim.Render(" best agent  ") +
			lg.NewStyle().Foreground(blue).Render("░") + dim.Render(" population avg") + "\n")
		left.WriteString(m.buildDualChart(r.Generations, leftW-4))
	}

	leftPane := lg.NewStyle().Width(leftW).Height(topH).Render(left.String())

	// Right: tree + scenarios
	var right strings.Builder

	// ── Winner's Lineage ──
	right.WriteString(amberText.Bold(true).Render(" Winner's Lineage") + "\n")
	right.WriteString(dim.Render(" score of the best agent across generations") + "\n\n")

	tree := m.buildEvolutionTree(r, rightW-2)
	right.WriteString(tree)

	right.WriteString("\n")

	// ── Population Health ──
	totalSurvived := 0
	totalEliminated := 0
	for _, gen := range r.Generations {
		totalSurvived += len(gen.Survivors)
		totalEliminated += len(gen.Eliminated)
	}
	right.WriteString(amberText.Bold(true).Render(" Population") + "\n")
	right.WriteString(fmt.Sprintf(" %s %d survived  %s %d eliminated  ",
		greenText.Render("▲"), totalSurvived,
		dim.Render("▼"), totalEliminated))
	right.WriteString(dim.Render(fmt.Sprintf("over %d gens", len(r.Generations))) + "\n")

	// Diversity sparkline
	if len(r.Generations) > 0 {
		right.WriteString(dim.Render(" genetic diversity: "))
		for i, gen := range r.Generations {
			d := gen.Diversity
			if d > 0.4 {
				right.WriteString(greenText.Render("█"))
			} else if d > 0.2 {
				right.WriteString(amberText.Render("▆"))
			} else if d > 0.1 {
				right.WriteString(amberText.Render("▃"))
			} else {
				right.WriteString(lg.NewStyle().Foreground(lg.Color("#FF5555")).Render("▁"))
			}
			_ = i
		}
		right.WriteString(dim.Render("  high=diverse") + "\n")
	}
	right.WriteString("\n")

	// ── Scenario Breakdown ──
	right.WriteString(cyanText.Bold(true).Render(" Scenario Breakdown") + "\n")
	right.WriteString(dim.Render(" how the winner scored on each test scenario") + "\n\n")
	if len(r.FinalScores) > 0 {
		var best *AgentScore
		for i := range r.FinalScores {
			if best == nil || r.FinalScores[i].Overall > best.Overall {
				best = &r.FinalScores[i]
			}
		}
		if best != nil {
			bestIdx, worstIdx := 0, 0
			for i, score := range best.ScenarioScores {
				name := fmt.Sprintf("S%d", i+1)
				if i < len(r.ScenarioNames) && r.ScenarioNames[i] != "" {
					name = r.ScenarioNames[i]
					if len(name) > rightW-12 {
						name = name[:rightW-13] + "…"
					}
				}
				barLen := rightW/3 - 2
				if barLen < 3 {
					barLen = 3
				}
				filled := int(score * float64(barLen))
				if filled > barLen {
					filled = barLen
				}
				// Color bars by performance
				barColor := purple
				if score >= 0.7 {
					barColor = green
				} else if score < 0.4 {
					barColor = lg.Color("#FF5555")
				}
				bar := lg.NewStyle().Foreground(barColor).Render(strings.Repeat("█", filled)) +
					dim.Render(strings.Repeat("░", barLen-filled))
				right.WriteString(fmt.Sprintf(" %s %.0f%%\n", bar, score*100))
				right.WriteString(softText.Render(fmt.Sprintf(" %s", name)) + "\n")

				if score > best.ScenarioScores[bestIdx] {
					bestIdx = i
				}
				if score < best.ScenarioScores[worstIdx] {
					worstIdx = i
				}
			}
			// Strength/weakness
			if len(best.ScenarioScores) > 1 {
				right.WriteString("\n")
				bestName := "?"
				worstName := "?"
				if bestIdx < len(r.ScenarioNames) {
					bestName = r.ScenarioNames[bestIdx]
				}
				if worstIdx < len(r.ScenarioNames) {
					worstName = r.ScenarioNames[worstIdx]
				}
				if len(bestName) > rightW-20 {
					bestName = bestName[:rightW-21] + "…"
				}
				if len(worstName) > rightW-20 {
					worstName = worstName[:rightW-21] + "…"
				}
				right.WriteString(greenText.Render(" ✦ strongest: ") + softText.Render(bestName) + "\n")
				right.WriteString(lg.NewStyle().Foreground(lg.Color("#FF5555")).Render(" ○ weakest:   ") + softText.Render(worstName) + "\n")
			}
		}
	}

	rightPane := lg.NewStyle().Width(rightW).Height(topH).Render(right.String())
	topRow := lg.JoinHorizontal(lg.Top, leftPane, rightPane)

	divider := dim.Render(strings.Repeat("─", w))

	genomeLabel := r.BestGenome
	if len(genomeLabel) > 12 {
		genomeLabel = genomeLabel[:12]
	}
	promptHeader := lg.NewStyle().Background(bgPanel).Foreground(brightBlue).
		Width(w).
		Render(fmt.Sprintf(" Evolved Agent Prompt · %s", genomeLabel))

	prompt := m.colorizePrompt(r.BestPrompt, w-4)
	scrolled := m.scrollView(prompt, botH-2, &m.resultScroll)

	return topRow + "\n" + divider + "\n" + promptHeader + "\n" + scrolled
}

func (m *ResultModel) colorizePrompt(prompt string, maxWidth int) string {
	var result strings.Builder
	for _, line := range strings.Split(prompt, "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "## ") {
			result.WriteString(blueText.Render(" "+trimmed) + "\n")
		} else if trimmed == "" {
			result.WriteString("\n")
		} else {
			wrapped := m.wordWrap(" "+line, maxWidth)
			result.WriteString(wrapped)
		}
	}
	return result.String()
}

func (m *ResultModel) buildEvolutionTree(r *EvolutionResult, w int) string {
	if len(r.AllScores) == 0 {
		return ""
	}

	lookup := make(map[string]*AgentScore)
	byGen := make(map[int][]AgentScore)
	for i := range r.AllScores {
		lookup[r.AllScores[i].GenomeID] = &r.AllScores[i]
		byGen[r.AllScores[i].Generation] = append(byGen[r.AllScores[i].Generation], r.AllScores[i])
	}

	lineage := make(map[string]bool)
	current := r.BestGenome
	for current != "" {
		lineage[current] = true
		if agent, ok := lookup[current]; ok && len(agent.ParentIDs) > 0 {
			current = agent.ParentIDs[0]
		} else {
			current = ""
		}
	}

	var b strings.Builder
	numGens := len(r.Generations)
	barLen := w/3 - 2
	if barLen < 3 {
		barLen = 3
	}

	for gen := 0; gen < numGens; gen++ {
		agents := byGen[gen]
		if len(agents) == 0 {
			continue
		}

		isLast := gen == numGens-1
		connector := dim.Render("├")
		if isLast {
			connector = dim.Render("└")
		}

		var winner *AgentScore
		others := 0
		for i := range agents {
			if lineage[agents[i].GenomeID] {
				winner = &agents[i]
			} else {
				others++
			}
		}

		if winner != nil {
			filled := int(winner.Overall * float64(barLen))
			if filled > barLen {
				filled = barLen
			}
			bar := lg.NewStyle().Foreground(purple).Render(strings.Repeat("█", filled)) +
				dim.Render(strings.Repeat("░", barLen-filled))

			marker := accent.Render("●")
			if isLast {
				marker = goldText.Render("★")
			}
			genLabel := dim.Render(fmt.Sprintf("G%d ", gen))
			b.WriteString(fmt.Sprintf("%s %s %s%s %.0f%% %s\n",
				connector, marker, genLabel, bar, winner.Overall*100,
				dim.Render(fmt.Sprintf("(%d agents)", len(agents)))))
		} else {
			b.WriteString(fmt.Sprintf("%s %s\n", connector,
				dim.Render(fmt.Sprintf("G%d (%d agents)", gen, len(agents)))))
		}
	}
	return b.String()
}

// ── Charts ──────────────────────────────────────────────────────────────────

func (m *ResultModel) buildDualChart(gens []GenerationStats, width int) string {
	if len(gens) == 0 {
		return ""
	}

	chartHeight := 7
	chartWidth := width - 8

	maxScore := 0.0
	for _, g := range gens {
		if g.BestScore > maxScore {
			maxScore = g.BestScore
		}
	}
	if maxScore < 0.1 {
		maxScore = 1.0
	}

	barWidth := chartWidth / len(gens)
	if barWidth < 4 {
		barWidth = 4
	}
	if barWidth > 8 {
		barWidth = 8
	}

	var lines []string
	for row := chartHeight; row >= 1; row-- {
		threshold := (float64(row) / float64(chartHeight)) * maxScore
		label := dim.Render(fmt.Sprintf(" %3.0f%% ", threshold*100))
		line := label + dim.Render("┊")
		for _, g := range gens {
			bestAbove := g.BestScore >= threshold
			avgAbove := g.AvgScore >= threshold
			if bestAbove {
				line += lg.NewStyle().Foreground(purple).Render(strings.Repeat("█", barWidth/2))
			} else {
				line += strings.Repeat(" ", barWidth/2)
			}
			if avgAbove {
				line += lg.NewStyle().Foreground(blue).Render(strings.Repeat("░", barWidth/2))
			} else {
				line += strings.Repeat(" ", barWidth/2)
			}
		}
		lines = append(lines, line)
	}

	axis := dim.Render("      ┊")
	for range gens {
		axis += dim.Render(strings.Repeat("─", barWidth))
	}
	lines = append(lines, axis)

	labels := "       "
	for i := range gens {
		labels += dim.Render(fmt.Sprintf("G%-*d", barWidth-1, i))
	}
	lines = append(lines, labels)

	return strings.Join(lines, "\n")
}

// ── Cell content styling ────────────────────────────────────────────────────

func (m *ResultModel) styleCellPreview(raw string, maxWidth, maxLines int) string {
	// Parse block markers and extract conversation snippets
	blocks := strings.Split(raw, "<<<\n")

	var styled []string
	for _, block := range blocks {
		block = strings.TrimSpace(block)
		if block == "" {
			continue
		}

		if strings.HasPrefix(block, ">>>") {
			content := strings.TrimPrefix(block, ">>>")
			// Take first line of agent message
			firstLine := strings.SplitN(strings.TrimSpace(content), "\n", 2)[0]
			if len(firstLine) > maxWidth-2 {
				firstLine = firstLine[:maxWidth-2] + "…"
			}
			styled = append(styled, lg.NewStyle().Foreground(purple).Render("▎")+" "+firstLine)
		} else if strings.HasPrefix(block, "<<<") {
			content := strings.TrimPrefix(block, "<<<")
			parts := strings.SplitN(content, "\n", 2)
			if len(parts) > 1 {
				firstLine := strings.SplitN(strings.TrimSpace(parts[1]), "\n", 2)[0]
				if len(firstLine) > maxWidth-2 {
					firstLine = firstLine[:maxWidth-2] + "…"
				}
				styled = append(styled, softText.Render("  "+firstLine))
			}
		}
	}

	if len(styled) > maxLines {
		styled = styled[len(styled)-maxLines:]
	}
	return strings.Join(styled, "\n")
}


func (m *ResultModel) styleDetailView(raw string, maxWidth int) string {
	// Parse block markers: >>>\n...\n<<< for agent, <<<Name\n...\n<<< for counterparty
	var result strings.Builder
	bar := lg.NewStyle().Foreground(purple).Render("  ┃ ")
	defaultAgentLabel := lg.NewStyle().Foreground(purple).Bold(true).Render("  agent")

	blocks := strings.Split(raw, "<<<\n")

	for _, block := range blocks {
		block = strings.TrimSpace(block)
		if block == "" {
			continue
		}

		if strings.HasPrefix(block, ">>>") {
			raw := strings.TrimPrefix(block, ">>>")
			// First line is agent name, rest is content
			parts := strings.SplitN(raw, "\n", 2)
			agentName := strings.TrimSpace(parts[0])
			content := ""
			if len(parts) > 1 {
				content = strings.TrimSpace(parts[1])
			}
			label := defaultAgentLabel
			if agentName != "" {
				label = lg.NewStyle().Foreground(purple).Bold(true).Render("  "+agentName) +
					dim.Render(" (agent)")
			}
			result.WriteString("\n" + label + "\n")
			wrapped := m.wordWrap(content, maxWidth-6)
			for _, wl := range strings.Split(wrapped, "\n") {
				if wl != "" {
					result.WriteString(bar + wl + "\n")
				}
			}
		} else if strings.HasPrefix(block, "<<<") {
			// Counterparty message — name is on the first line after <<<
			content := strings.TrimPrefix(block, "<<<")
			parts := strings.SplitN(content, "\n", 2)
			name := strings.TrimSpace(parts[0])
			body := ""
			if len(parts) > 1 {
				body = strings.TrimSpace(parts[1])
			}
			nameLabel := lg.NewStyle().Foreground(cyan).Bold(true).Render("  "+name) +
				dim.Render(" (counterparty)")
			result.WriteString("\n" + nameLabel + "\n")
			if body != "" {
				wrapped := m.wordWrap(body, maxWidth-6)
				for _, wl := range strings.Split(wrapped, "\n") {
					if wl != "" {
						result.WriteString("    " + wl + "\n")
					}
				}
			}
		} else {
			// System message (generation markers, chat ended, etc)
			for _, line := range strings.Split(block, "\n") {
				line = strings.TrimSpace(line)
				if line == "" {
					continue
				}
				if strings.Contains(line, "chat ended") {
					result.WriteString("\n" + softText.Render("  "+line) + "\n")
				} else if strings.Contains(line, "Gen ") || strings.Contains(line, "Generation") {
					result.WriteString("\n" + amberText.Render("  "+line) + "\n")
				} else {
					result.WriteString("\n" + dim.Render("  "+line) + "\n")
				}
			}
		}
	}
	return result.String()
}

// ── Helpers ─────────────────────────────────────────────────────────────────

func (m *ResultModel) scrollView(wrapped string, visibleLines int, scroll *int) string {
	allLines := strings.Split(wrapped, "\n")
	end := len(allLines) - *scroll
	if end > len(allLines) {
		end = len(allLines)
	}
	if end < 0 {
		end = 0
	}
	start := end - visibleLines
	if start < 0 {
		start = 0
	}
	maxScroll := len(allLines) - visibleLines
	if maxScroll < 0 {
		maxScroll = 0
	}
	if *scroll > maxScroll {
		*scroll = maxScroll
	}
	return strings.Join(allLines[start:end], "\n")
}

func (m *ResultModel) allDone() bool {
	for i := range m.cells {
		if m.cells[i].status != StatusDone {
			return false
		}
	}
	return true
}

func (m *ResultModel) getLastLines(content string, maxLines int) string {
	if maxLines <= 0 {
		return ""
	}
	lines := strings.Split(content, "\n")
	if len(lines) <= maxLines {
		return content
	}
	return strings.Join(lines[len(lines)-maxLines:], "\n")
}

func (m *ResultModel) wordWrap(content string, maxWidth int) string {
	if maxWidth <= 0 {
		return content
	}
	var result strings.Builder
	for _, line := range strings.Split(content, "\n") {
		if len([]rune(line)) <= maxWidth {
			result.WriteString(line + "\n")
			continue
		}
		words := strings.Fields(line)
		cur := ""
		for _, word := range words {
			if cur == "" {
				cur = word
			} else if len([]rune(cur))+1+len([]rune(word)) <= maxWidth {
				cur += " " + word
			} else {
				result.WriteString(cur + "\n")
				cur = word
			}
		}
		if cur != "" {
			result.WriteString(cur + "\n")
		}
	}
	return result.String()
}

func (m *ResultModel) truncateLines(content string, maxWidth int) string {
	if maxWidth <= 0 {
		return ""
	}
	lines := strings.Split(content, "\n")
	for i, line := range lines {
		runes := []rune(line)
		if len(runes) > maxWidth {
			lines[i] = string(runes[:maxWidth]) + "…"
		}
	}
	return strings.Join(lines, "\n")
}

func (m *ResultModel) truncStr(s string, max int) string {
	if len(s) <= max {
		return s
	}
	if max < 4 {
		return s[:max]
	}
	return s[:max-1] + "…"
}
