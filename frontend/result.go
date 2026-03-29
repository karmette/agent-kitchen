package main

import (
	"fmt"
	"strings"
	"time"

	tea "charm.land/bubbletea/v2"
	lg "charm.land/lipgloss/v2"
)

type tickMsg time.Time

var (
	resultContainer = lg.NewStyle().
			Border(lg.DoubleBorder()).
			BorderForeground(lg.Color("#3993ED")).
			Padding(1, 2)

	cellStyle = lg.NewStyle().
			Border(lg.NormalBorder()).
			BorderForeground(lg.Color("#CCCCCC"))

	doneCellStyle = lg.NewStyle().
			Border(lg.NormalBorder()).
			BorderForeground(lg.Color("#81F88F"))
	focusedCellStyle = lg.NewStyle().
				Border(lg.NormalBorder()).
				BorderForeground(lg.Color("#09588A"))

	sidePanelStyle = lg.NewStyle().
			Border(lg.RoundedBorder()).
			BorderForeground(lg.Color("#96BEFD")).
			PaddingLeft(2)

	listItemStyle = lg.NewStyle().
			Foreground(lg.Color("#FAFAFA")).
			PaddingLeft(2)

	focusedListItemStyle = lg.NewStyle().
				UnderlineColor(lg.Color("#96BEFD")).
				PaddingLeft(2).
				UnderlineStyle(lg.UnderlineSingle)
)

type CellStatus string

const (
	StatusPending CellStatus = "pending"
	StatusRunning CellStatus = "running"
	StatusDone    CellStatus = "done"
	StatusError   CellStatus = "error"
)

type cell struct {
	status   CellStatus
	conv     conversation
	content  strings.Builder
	scenario string // scenario name for this cell
}
type ViewMode int

const (
	ModeGrid ViewMode = iota
	ModeDetail
	ModeResult
)

type ResultModel struct {
	content      string
	rows         int
	cols         int
	iterations   int
	focusedRow   int
	focusedCol   int
	width        int
	height       int
	cells        []cell
	mode         ViewMode
	detailScroll int
	resultScroll int
}

func NewResultModel(rows, cols, iterations, numCells int) *ResultModel {
	cells := make([]cell, numCells)

	for i := range cells {
		cells[i].status = StatusPending
		cells[i].conv = newConversation()
	}
	return &ResultModel{
		content:    "",
		rows:       rows,
		cols:       cols,
		iterations: iterations,
		focusedRow: 0,
		focusedCol: 0,
		width:      0,
		height:     0,
		cells:      cells,
		mode:       ModeGrid,
	}
}

func (m *ResultModel) Init() tea.Cmd {
	return tea.Tick(time.Millisecond*100, func(t time.Time) tea.Msg {
		return tickMsg(t)
	})
}

func (m *ResultModel) Update(msg tea.Msg) tea.Cmd {
	m.checkChannels()

	switch msg := msg.(type) {
	case tickMsg:
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
				if FinalResult != nil && m.allDone() {
					m.mode = ModeResult
					m.resultScroll = 0
				} else if idx < len(m.cells) {
					m.mode = ModeDetail
					m.detailScroll = 0
				}
			}
		case "r":
			// Quick key to view results if available
			if FinalResult != nil {
				m.mode = ModeResult
				m.resultScroll = 0
			}
		case "esc":
			if m.mode == ModeDetail || m.mode == ModeResult {
				m.mode = ModeGrid
			}
		case "h", "left":
			if m.mode == ModeGrid {
				if m.focusedCol > 0 {
					m.focusedCol--
				} else {
					m.focusedCol = m.cols - 1
					if m.focusedRow > 0 {
						m.focusedRow--
					} else {
						m.focusedRow = m.rows - 1
					}
				}
			}
		case "l", "right":
			if m.mode == ModeGrid {
				if m.focusedCol < m.cols-1 {
					m.focusedCol++
				} else {
					m.focusedCol = 0
					if m.focusedRow < m.rows-1 {
						m.focusedRow++
					} else {
						m.focusedRow = 0
					}
				}
			}
		case "k", "up":
			if m.mode == ModeDetail {
				m.detailScroll++
			} else if m.mode == ModeResult {
				m.resultScroll++
			} else if m.mode == ModeGrid {
				if m.focusedRow > 0 {
					m.focusedRow--
				} else {
					m.focusedRow = m.rows - 1
				}
			}
		case "j", "down":
			if m.mode == ModeDetail {
				if m.detailScroll > 0 {
					m.detailScroll--
				}
			} else if m.mode == ModeResult {
				if m.resultScroll > 0 {
					m.resultScroll--
				}
			} else if m.mode == ModeGrid {
				if m.focusedRow < m.rows-1 {
					m.focusedRow++
				} else {
					m.focusedRow = 0
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

	// Auto-switch to results only from grid view — don't interrupt detail view
	if m.mode == ModeGrid && FinalResult != nil && m.allDone() {
		m.mode = ModeResult
		m.resultScroll = 0
	}
}

func (m *ResultModel) getLastLines(content string, maxLines int) string {
	lines := strings.Split(content, "\n")
	if len(lines) <= maxLines {
		return content
	}
	return strings.Join(lines[len(lines)-maxLines:], "\n")
}

func (m *ResultModel) View() string {
	if m.width == 0 || m.height == 0 {
		return resultContainer.Render("Initializing...")
	}

	sidePanelWidth := 30

	containerStyle := resultContainer.Width(m.width).Height(m.height)
	innerWidth := m.width - resultContainer.GetHorizontalFrameSize()
	innerHeight := m.height - resultContainer.GetVerticalFrameSize()
	if m.mode == ModeDetail {
		idx := m.focusedRow*m.cols + m.focusedCol
		if idx >= len(m.cells) {
			return containerStyle.Render("No cell selected")
		}

		// Subtle header bar with scenario name
		cellInfo := m.cells[idx].scenario
		if cellInfo == "" {
			cellInfo = fmt.Sprintf("Cell %d", idx+1)
		}
		status := string(m.cells[idx].status)
		header := lg.NewStyle().Foreground(lg.Color("#555555")).
			Render(cellInfo + " · " + status + " · ↑↓ scroll · esc back")

		raw := m.cells[idx].content.String()
		wrapped := m.wordWrap(raw, innerWidth-4)
		allLines := strings.Split(wrapped, "\n")

		// Scroll: detailScroll=0 means bottom (latest), higher = further back
		visibleLines := innerHeight - 2 // header + bottom margin
		end := len(allLines) - m.detailScroll
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
		if m.detailScroll > len(allLines)-visibleLines {
			m.detailScroll = len(allLines) - visibleLines
			if m.detailScroll < 0 {
				m.detailScroll = 0
			}
		}

		chatContent := strings.Join(allLines[start:end], "\n")

		return containerStyle.Render(header + "\n" + chatContent)
	}

	// Result view: show the evolved agent's prompt
	if m.mode == ModeResult && FinalResult != nil {
		title := lg.NewStyle().Foreground(lg.Color("#FFD700")).Bold(true).
			Render("★ EVOLVED AGENT ★")
		score := lg.NewStyle().Foreground(lg.Color("#888888")).
			Render(fmt.Sprintf("Score: %.2f · Genome: %s", FinalResult.BestScore, FinalResult.BestGenome[:12]))
		hint := lg.NewStyle().Foreground(lg.Color("#555555")).
			Render("↑↓ scroll · esc back")

		content := title + "\n" + score + "\n" + hint + "\n\n" + FinalResult.BestPrompt
		wrapped := m.wordWrap(content, innerWidth-4)
		allLines := strings.Split(wrapped, "\n")

		visibleLines := innerHeight - 1
		end := len(allLines) - m.resultScroll
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
		if m.resultScroll > len(allLines)-visibleLines {
			m.resultScroll = len(allLines) - visibleLines
			if m.resultScroll < 0 {
				m.resultScroll = 0
			}
		}

		return containerStyle.Render(strings.Join(allLines[start:end], "\n"))
	}

	gridWidth := innerWidth - sidePanelWidth - 1
	cellWidth := gridWidth / m.cols
	cellHeight := innerHeight / m.rows

	var gridRows []string
	for r := 0; r < m.rows; r++ {
		var cells []string
		for c := 0; c < m.cols; c++ {
			var cell lg.Style
			if r == m.focusedRow && c == m.focusedCol {
				cell = focusedCellStyle
			} else {
				cell = cellStyle
			}
			idx := r*m.cols + c
			cell = cell.Width(cellWidth).Height(cellHeight)

			if idx >= len(m.cells) {
				// Empty slot — no cell for this grid position
				cells = append(cells, cell.Render(""))
				continue
			}

			cellContent := m.getLastLines(m.cells[idx].content.String(), cellHeight-2)
			maxLineWidth := cellWidth - 5
			cellContent = m.truncateLines(cellContent, maxLineWidth)
			cells = append(cells, cell.Render(cellContent))
		}
		gridRows = append(gridRows, lg.JoinHorizontal(lg.Top, cells...))
	}
	gridContent := lg.JoinVertical(lg.Top, gridRows...)

	var listItems []string
	for i := 0; i < m.rows*m.cols; i++ {
		row := i / m.cols
		col := i % m.cols
		cellNum := i + 1

		if i >= len(m.cells) {
			continue
		}

		status := m.cells[i].status
		item := fmt.Sprintf("Cell %d: %s", cellNum, status)

		var style lg.Style
		if row == m.focusedRow && col == m.focusedCol {
			style = focusedListItemStyle
		} else {
			style = listItemStyle
		}
		listItems = append(listItems, style.Render(item))
	}
	sideContent := lg.JoinVertical(lg.Top, listItems...)

	content := lg.JoinHorizontal(lg.Top,
		gridContent,
		sidePanelStyle.Width(sidePanelWidth).Height(innerHeight).Render(sideContent),
	)
	return containerStyle.Render(content)
}
func (m *ResultModel) allDone() bool {
	for i := range m.cells {
		if m.cells[i].status != StatusDone {
			return false
		}
	}
	return true
}

func (m *ResultModel) wordWrap(content string, maxWidth int) string {
	if maxWidth <= 0 {
		return content
	}
	var result strings.Builder
	for _, line := range strings.Split(content, "\n") {
		if len([]rune(line)) <= maxWidth {
			result.WriteString(line)
			result.WriteString("\n")
			continue
		}
		words := strings.Fields(line)
		current := ""
		for _, word := range words {
			if current == "" {
				current = word
			} else if len([]rune(current))+1+len([]rune(word)) <= maxWidth {
				current += " " + word
			} else {
				result.WriteString(current)
				result.WriteString("\n")
				current = word
			}
		}
		if current != "" {
			result.WriteString(current)
			result.WriteString("\n")
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
			// Truncate and append ...
			lines[i] = string(runes[:maxWidth]) + "..."
		}
	}

	return strings.Join(lines, "\n")
}
